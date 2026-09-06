package com.madhu.atlas

import android.app.Application
import android.content.Context
import com.madhu.atlas.agent.AgentLoop
import com.madhu.atlas.agent.SystemPrompt
import com.madhu.atlas.agent.ToolRegistry
import com.madhu.atlas.actions.ActionExecutor
import com.madhu.atlas.actions.ConsentBroker
import com.madhu.atlas.actions.DeterministicCommandParser
import com.madhu.atlas.chat.ConversationRepository
import com.madhu.atlas.data.Connectivity
import com.madhu.atlas.data.Secrets
import com.madhu.atlas.data.SettingsStore
import com.madhu.atlas.llm.DeepSeekEngine
import com.madhu.atlas.llm.UnavailableEngine
import com.madhu.atlas.llm.EngineRouter
import com.madhu.atlas.llm.LlmEngine
import com.madhu.atlas.profile.AtlasDatabase
import com.madhu.atlas.profile.ProfileStore
import com.madhu.atlas.tools.Notifications
import com.madhu.atlas.tools.profileTools

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
    val conversations = ConversationRepository(db.conversationDao())
    val commandParser = DeterministicCommandParser()
    val consentBroker = ConsentBroker(db.actionDao(), ActionExecutor(appContext))

    // Long-term profile facts.
    private val profile = ProfileStore(db.profileDao())

    // One real conversation engine. Offline mode fails explicitly instead of simulating an answer.
    private val local: LlmEngine = UnavailableEngine()
    private val online: LlmEngine = DeepSeekEngine(apiKeyProvider = { secrets.deepSeekApiKey })
    private val router = EngineRouter(local, online, connectivity, settings)

    // Device actions are deliberately excluded: only the deterministic command parser
    // may propose them through the consent broker. The LLM retains explicit fact tools.
    private val systemPrompt = SystemPrompt(profile)
    val agentLoop = AgentLoop(
        router,
        systemPrompt,
        ToolRegistry(profileTools(profile)),
    )
}
