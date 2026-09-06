package com.madhu.atlas

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import android.widget.Toast
import androidx.lifecycle.lifecycleScope
import com.madhu.atlas.tools.SpotifyController
import com.madhu.atlas.ui.ChatScreen
import com.madhu.atlas.ui.theme.AtlasTheme
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        setContent {
            AtlasTheme {
                Surface(Modifier.fillMaxSize()) {
                    ChatScreen()
                }
            }
        }
        handleSpotifyCallback(intent)
    }

    /** Launch the Spotify account login so ATLAS can reach the user's liked songs/playlists. */
    fun connectSpotify() {
        if (BuildConfig.SPOTIFY_CLIENT_ID.isBlank()) {
            Toast.makeText(this, "Spotify not configured in this build.", Toast.LENGTH_SHORT).show()
            return
        }
        val secrets = (application as AtlasApp).container.secrets
        val request = SpotifyController.authorizationRequest()
        secrets.spotifyPkceVerifier = request.verifier
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(request.url)))
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleSpotifyCallback(intent)
    }

    private fun handleSpotifyCallback(intent: Intent?) {
        val uri = intent?.data ?: return
        if (uri.scheme != "com.madhu.atlas" || uri.host != "callback") return
        val error = uri.getQueryParameter("error")
        val code = uri.getQueryParameter("code")
        val secrets = (application as AtlasApp).container.secrets
        val verifier = secrets.spotifyPkceVerifier
        secrets.spotifyPkceVerifier = null
        if (error != null || code == null || verifier == null) {
            Toast.makeText(this, "Spotify login was not completed.", Toast.LENGTH_SHORT).show()
            return
        }
        lifecycleScope.launch {
            val ok = SpotifyController.exchangeCode(code, verifier, secrets)
            Toast.makeText(this@MainActivity,
                if (ok) "Spotify connected." else "Spotify connection failed.", Toast.LENGTH_SHORT).show()
        }
    }

}
