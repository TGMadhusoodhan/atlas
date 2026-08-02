package com.madhu.atlas.llm

import com.madhu.atlas.agent.ToolSpec
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import java.util.concurrent.TimeUnit

/**
 * Online engine: streams from DeepSeek's OpenAI-compatible chat API over SSE.
 * Mirrors the request shape and tool schema used by the desktop `ai_helper.py`.
 *
 * The API key is supplied per-request by the router (read from encrypted storage),
 * never hard-coded.
 */
class DeepSeekEngine(
    private val apiKeyProvider: () -> String?,
    private val model: String = DEFAULT_MODEL,
    private val baseUrl: String = "https://api.deepseek.com",
    private val client: OkHttpClient = defaultClient(),
) : LlmEngine {

    override val id: String = "deepseek"

    override fun isAvailable(): Boolean = !apiKeyProvider().isNullOrBlank()

    override fun generate(messages: List<LlmMessage>, tools: List<ToolSpec>): Flow<LlmChunk> =
        callbackFlow {
            val key = apiKeyProvider()
            if (key.isNullOrBlank()) {
                trySend(LlmChunk.Failure("No DeepSeek API key set (add it in Settings)."))
                close()
                return@callbackFlow
            }

            val bodyJson = buildRequestBody(messages, tools)
            val request = Request.Builder()
                .url("$baseUrl/v1/chat/completions")
                .header("Authorization", "Bearer $key")
                .header("Accept", "text/event-stream")
                .post(bodyJson.toString().toRequestBody(JSON_MEDIA))
                .build()

            // Accumulates streamed tool-call fragments by their array index.
            val toolAcc = mutableMapOf<Int, ToolCallAccumulator>()

            val listener = object : EventSourceListener() {
                override fun onEvent(
                    eventSource: EventSource,
                    id: String?,
                    type: String?,
                    data: String,
                ) {
                    if (data == "[DONE]") {
                        toolAcc.toSortedMap().values.forEach { a ->
                            if (a.name.isNotBlank()) {
                                trySend(
                                    LlmChunk.ToolCallReceived(
                                        RawToolCall(
                                            id = a.id.ifBlank { "call_${a.name}" },
                                            name = a.name,
                                            argumentsJson = a.arguments.ifBlank { "{}" },
                                        )
                                    )
                                )
                            }
                        }
                        trySend(LlmChunk.Done("stop"))
                        close()
                        return
                    }
                    runCatching {
                        val obj = json.parseToJsonElement(data).jsonObject
                        val choice = obj["choices"]?.jsonArray?.firstOrNull()?.jsonObject
                            ?: return
                        val delta = choice["delta"]?.jsonObject
                        delta?.get("content")?.jsonPrimitive?.contentOrNull
                            ?.takeIf { it.isNotEmpty() }
                            ?.let { trySend(LlmChunk.Token(it)) }
                        delta?.get("tool_calls")?.jsonArray?.forEach { accumulateToolCall(toolAcc, it.jsonObject) }
                    }
                }

                override fun onClosed(eventSource: EventSource) {
                    close()
                }

                override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                    val msg = when {
                        response != null -> "DeepSeek HTTP ${response.code}: ${response.message}"
                        t != null -> t.message ?: "network error"
                        else -> "unknown DeepSeek error"
                    }
                    trySend(LlmChunk.Failure(msg, t))
                    close()
                }
            }

            val source = EventSources.createFactory(client).newEventSource(request, listener)
            awaitClose { source.cancel() }
        }

    private fun accumulateToolCall(acc: MutableMap<Int, ToolCallAccumulator>, tc: JsonObject) {
        val index = tc["index"]?.jsonPrimitive?.intOrNull ?: 0
        val a = acc.getOrPut(index) { ToolCallAccumulator() }
        tc["id"]?.jsonPrimitive?.contentOrNull?.let { a.id = it }
        tc["function"]?.jsonObject?.let { fn ->
            fn["name"]?.jsonPrimitive?.contentOrNull?.let { a.name = it }
            fn["arguments"]?.jsonPrimitive?.contentOrNull?.let { a.arguments += it }
        }
    }

    private fun buildRequestBody(messages: List<LlmMessage>, tools: List<ToolSpec>): JsonObject =
        buildJsonObject {
            put("model", model)
            put("stream", true)
            putJsonArray("messages") { messages.forEach { add(it.toJson()) } }
            if (tools.isNotEmpty()) {
                putJsonArray("tools") { tools.forEach { add(it.toJson()) } }
                put("tool_choice", "auto")
            }
        }

    private fun LlmMessage.toJson(): JsonObject = buildJsonObject {
        put("role", role.wire)
        put("content", content)
        toolCallId?.let { put("tool_call_id", it) }
        name?.let { put("name", it) }
        if (toolCalls.isNotEmpty()) {
            putJsonArray("tool_calls") {
                toolCalls.forEach { call ->
                    add(buildJsonObject {
                        put("id", call.id)
                        put("type", "function")
                        putJsonObject("function") {
                            put("name", call.name)
                            put("arguments", call.argumentsJson)
                        }
                    })
                }
            }
        }
    }

    private fun ToolSpec.toJson(): JsonObject = buildJsonObject {
        put("type", "function")
        putJsonObject("function") {
            put("name", name)
            put("description", description)
            put("parameters", runCatching { json.parseToJsonElement(parametersJson) }
                .getOrElse { JsonObject(emptyMap()) })
        }
    }

    private class ToolCallAccumulator {
        var id: String = ""
        var name: String = ""
        var arguments: String = ""
    }

    companion object {
        const val DEFAULT_MODEL = "deepseek-v4-flash"
        private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
        private val json = Json { ignoreUnknownKeys = true; isLenient = true }

        private fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS)   // SSE stream stays open
            .writeTimeout(20, TimeUnit.SECONDS)
            .build()
    }
}
