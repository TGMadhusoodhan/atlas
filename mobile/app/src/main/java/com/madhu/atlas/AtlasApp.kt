package com.madhu.atlas

import android.app.Application
import android.content.Context
import android.util.Log
import com.madhu.atlas.agent.AgentLoop
import com.madhu.atlas.agent.SystemPrompt
import com.madhu.atlas.agent.ToolRegistry
import com.madhu.atlas.data.Connectivity
import com.madhu.atlas.data.Secrets
import com.madhu.atlas.data.SettingsStore
import com.madhu.atlas.llm.DeepSeekEngine
import com.madhu.atlas.llm.EchoEngine
import com.madhu.atlas.llm.EngineRouter
import com.madhu.atlas.llm.LlmEngine
import com.madhu.atlas.memory.Embedder
import com.madhu.atlas.memory.MemoryStore
import com.madhu.atlas.profile.AtlasDatabase
import com.madhu.atlas.profile.ProfileStore
import com.madhu.atlas.tools.Notifications
import com.madhu.atlas.tools.deviceTools

class AtlasApp : Application() {
    lateinit var container: AtlasContainer
        private set

    override fun onCreate() {
        super.onCreate()
        Notifications.ensureChannel(this)
        container = AtlasContainer(this)
    }
}

/**
 * Tiny hand-rolled DI container. Builds the M1 object graph once and hands the
 * [AgentLoop] + stores to the ViewModel. (No DI framework — the graph is small and
 * this keeps the wiring readable.)
 */
class AtlasContainer(context: Context) {
    private val appContext = context.applicationContext

    val secrets = Secrets(appContext)
    val settings = SettingsStore(appContext)
    private val connectivity = Connectivity(appContext)

    private val db = AtlasDatabase.get(appContext)

    // Semantic memory. Embedder needs the MiniLM assets; if absent, memory runs
    // disabled (embedder = null) and chat is unaffected — same fail-soft as desktop.
    private val embedder: Embedder? = runCatching { Embedder.create(appContext) }
        .onFailure { Log.w("ATLAS", "Embedder unavailable — memory disabled: ${it.message}") }
        .getOrNull()
    val memory = MemoryStore.create(db.memoryDao(), embedder)

    // Long-term profile facts.
    private val profile = ProfileStore(db.profileDao())

    // Engines: Echo is the offline placeholder until GenieEngine (NPU) is wired in M1 step 5.
    private val local: LlmEngine = EchoEngine()
    private val online: LlmEngine = DeepSeekEngine(apiKeyProvider = { secrets.deepSeekApiKey })
    private val router = EngineRouter(local, online, connectivity, settings)

    // The reused agent core, now with the M2 device toolset.
    private val systemPrompt = SystemPrompt(profile, memory)
    val agentLoop = AgentLoop(router, systemPrompt, ToolRegistry(deviceTools(appContext)), memory)
}
