package com.madhu.atlas.agent

import kotlinx.serialization.json.JsonObject

/**
 * Declarative description of a tool the model may call. Mirrors the `TOOLS` entries
 * in the desktop `ai_helper.py` (OpenAI function schema). [parametersJson] is a raw
 * JSON-Schema object string describing the arguments.
 */
data class ToolSpec(
    val name: String,
    val description: String,
    val parametersJson: String,
)

/** Result of running a tool. [ok] false means the model should see an error. */
data class ToolResult(val ok: Boolean, val content: String)

/**
 * An executable tool. M1 ships none (the brain milestone); M2 adds Spotify, camera,
 * dialer, mail and reminders. Kept here so the agent loop and both engines already
 * speak the tool protocol.
 */
interface Tool {
    val spec: ToolSpec

    /** Run the tool with parsed [args]. Suspends — tools may do IO / await user consent. */
    suspend fun run(args: JsonObject): ToolResult
}

/** Lookup + advertisement surface for the agent loop. */
class ToolRegistry(tools: List<Tool> = emptyList()) {
    private val byName: Map<String, Tool> = tools.associateBy { it.spec.name }

    fun specs(): List<ToolSpec> = byName.values.map { it.spec }

    fun find(name: String): Tool? = byName[name]

    val isEmpty: Boolean get() = byName.isEmpty()
}
