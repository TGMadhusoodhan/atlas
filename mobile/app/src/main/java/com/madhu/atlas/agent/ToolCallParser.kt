package com.madhu.atlas.agent

import com.madhu.atlas.llm.RawToolCall
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Extracts tool calls from a *token-only* engine's output (the Genie/NPU path, which
 * has no native tool-calling). The agreed protocol: the model emits a single JSON
 * object `{"tool":"name","args":{...}}` (optionally fenced in ```json) when it wants a
 * tool. DeepSeek uses native tool_calls instead and never needs this.
 *
 * M1 ships no tools, so this is exercised in M2; it's here so the token path already
 * speaks the protocol.
 */
object ToolCallParser {
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    /** Returns a [RawToolCall] if [text] is (or contains) a tool-call JSON object, else null. */
    fun parse(text: String): RawToolCall? {
        val candidate = extractJsonObject(text) ?: return null
        return runCatching {
            val obj = json.parseToJsonElement(candidate).jsonObject
            val name = obj["tool"]?.jsonPrimitive?.contentOrNull ?: return null
            val args = (obj["args"] as? JsonObject)?.toString() ?: "{}"
            RawToolCall(id = "call_$name", name = name, argumentsJson = args)
        }.getOrNull()
    }

    /** Pull the first balanced {...} block, tolerating ```json fences and surrounding prose. */
    private fun extractJsonObject(raw: String): String? {
        val text = raw.trim().removePrefix("```json").removePrefix("```").removeSuffix("```").trim()
        val start = text.indexOf('{')
        if (start < 0) return null
        var depth = 0
        for (i in start until text.length) {
            when (text[i]) {
                '{' -> depth++
                '}' -> {
                    depth--
                    if (depth == 0) return text.substring(start, i + 1)
                }
            }
        }
        return null
    }
}
