package com.madhu.atlas.tools

import android.content.Context
import android.util.Log
import com.madhu.atlas.BuildConfig
import com.madhu.atlas.data.Secrets
import com.spotify.android.appremote.api.ConnectionParams
import com.spotify.android.appremote.api.Connector
import com.spotify.android.appremote.api.SpotifyAppRemote
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.SecureRandom
import android.util.Base64
import kotlin.coroutines.resume

/**
 * Spotify playback: resolve a query to a URI and play it on the phone via App Remote.
 *
 * OAuth Authorization Code with PKCE treats this APK as a public client: no client secret
 * is embedded. The user refresh token unlocks their own **liked songs**
 *    and **playlists**. Obtained once via [exchangeCode] from the login flow.
 *
 * All optional: [configured] is false without credentials and the caller falls back to an
 * intent. The public client id comes from local.properties (BuildConfig).
 */
object SpotifyController {

    val configured: Boolean
        get() = BuildConfig.SPOTIFY_CLIENT_ID.isNotBlank()

    fun connected(secrets: Secrets): Boolean = secrets.spotifyRefreshToken != null

    private val http = OkHttpClient()
    private var userToken: String? = null
    private var userTokenExpiry = 0L
    private var appRemote: SpotifyAppRemote? = null

    private const val TOKEN_URL = "https://accounts.spotify.com/api/token"
    private const val API = "https://api.spotify.com/v1"

    // ── auth ────────────────────────────────────────────────────────────────────

    data class Authorization(val url: String, val verifier: String)

    fun authorizationRequest(): Authorization {
        val bytes = ByteArray(64).also { SecureRandom().nextBytes(it) }
        val verifier = Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        val challenge = Base64.encodeToString(
            MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray()),
            Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING,
        )
        val scopes = "user-library-read playlist-read-private user-modify-playback-state user-read-playback-state app-remote-control streaming"
        val url = "https://accounts.spotify.com/authorize?response_type=code" +
            "&client_id=${enc(BuildConfig.SPOTIFY_CLIENT_ID)}" +
            "&redirect_uri=${enc(BuildConfig.SPOTIFY_REDIRECT_URI)}" +
            "&code_challenge_method=S256&code_challenge=${enc(challenge)}&scope=${enc(scopes)}"
        return Authorization(url, verifier)
    }

    /** Exchange a login auth-code with its one-time PKCE verifier. */
    suspend fun exchangeCode(code: String, verifier: String, secrets: Secrets): Boolean = withContext(Dispatchers.IO) {
        val form = FormBody.Builder()
            .add("grant_type", "authorization_code")
            .add("code", code)
            .add("redirect_uri", BuildConfig.SPOTIFY_REDIRECT_URI)
            .add("client_id", BuildConfig.SPOTIFY_CLIENT_ID)
            .add("code_verifier", verifier)
            .build()
        val json = postToken(form) ?: return@withContext false
        json.optString("refresh_token").takeIf { it.isNotBlank() }?.let { secrets.spotifyRefreshToken = it }
        cacheUserToken(json)
        secrets.spotifyRefreshToken != null
    }

    private suspend fun userToken(secrets: Secrets): String? {
        val now = System.currentTimeMillis()
        userToken?.let { if (now < userTokenExpiry) return it }
        val refresh = secrets.spotifyRefreshToken ?: return null
        val form = FormBody.Builder()
            .add("grant_type", "refresh_token")
            .add("refresh_token", refresh)
            .add("client_id", BuildConfig.SPOTIFY_CLIENT_ID)
            .build()
        val json = withContext(Dispatchers.IO) { postToken(form) } ?: return null
        // Spotify may rotate the refresh token; keep the new one if present.
        json.optString("refresh_token").takeIf { it.isNotBlank() }?.let { secrets.spotifyRefreshToken = it }
        return cacheUserToken(json)
    }

    private fun cacheUserToken(json: JSONObject): String? {
        val access = json.optString("access_token").takeIf { it.isNotBlank() } ?: return null
        userToken = access
        userTokenExpiry = System.currentTimeMillis() + (json.optLong("expires_in", 3600) - 60) * 1000
        return access
    }

    private fun postToken(form: FormBody): JSONObject? {
        val req = Request.Builder().url(TOKEN_URL).post(form).build()
        return runCatching {
            http.newCall(req).execute().use { r ->
                if (!r.isSuccessful) null else JSONObject(r.body?.string() ?: return@use null)
            }
        }.getOrNull()
    }

    // ── resolve a query to a playable URI ─────────────────────────────────────────

    /** Top public-catalog track for a query. Uses the user token if present, else app token. */
    suspend fun searchCatalog(query: String, secrets: Secrets): String? = withContext(Dispatchers.IO) {
        val tok = userToken(secrets) ?: return@withContext null
        get("$API/search?type=track&limit=1&q=${enc(query)}", tok)?.let { json ->
            val items = json.getJSONObject("tracks").getJSONArray("items")
            if (items.length() == 0) null else items.getJSONObject(0).getString("uri")
        }
    }

    /** Find a track in the user's Liked Songs matching [query] (blank → first saved). */
    suspend fun searchLiked(query: String, secrets: Secrets): String? = withContext(Dispatchers.IO) {
        val tok = userToken(secrets) ?: return@withContext null
        val q = query.trim().lowercase()
        var url: String? = "$API/me/tracks?limit=50"
        var pages = 0
        while (url != null && pages < 5) {
            val json = get(url, tok) ?: break
            val items = json.getJSONArray("items")
            for (i in 0 until items.length()) {
                val track = items.getJSONObject(i).getJSONObject("track")
                val name = track.getString("name")
                val artists = (0 until track.getJSONArray("artists").length()).joinToString(" ") {
                    track.getJSONArray("artists").getJSONObject(it).getString("name")
                }
                val hay = "$name $artists".lowercase()
                if (q.isBlank() || hay.contains(q) || q.split(" ").all { hay.contains(it) }) {
                    return@withContext track.getString("uri")
                }
            }
            url = json.optString("next").takeIf { it.isNotBlank() && it != "null" }
            pages++
        }
        null
    }

    /** Find one of the user's playlists by name; returns its context URI. */
    suspend fun findPlaylist(name: String, secrets: Secrets): String? = withContext(Dispatchers.IO) {
        val tok = userToken(secrets) ?: return@withContext null
        val q = name.trim().lowercase()
        val json = get("$API/me/playlists?limit=50", tok) ?: return@withContext null
        val items = json.getJSONArray("items")
        for (i in 0 until items.length()) {
            val pl = items.getJSONObject(i)
            if (pl.getString("name").lowercase().contains(q)) return@withContext pl.getString("uri")
        }
        null
    }

    private fun get(url: String, token: String): JSONObject? {
        val req = Request.Builder().url(url).header("Authorization", "Bearer $token").build()
        return runCatching {
            http.newCall(req).execute().use { r ->
                if (!r.isSuccessful) null else JSONObject(r.body?.string() ?: return@use null)
            }
        }.getOrNull()
    }

    // ── playback ──────────────────────────────────────────────────────────────────

    /** Play a track/playlist/album URI on the phone via App Remote. */
    suspend fun play(context: Context, uri: String): Boolean = withContext(Dispatchers.Main) {
        withTimeoutOrNull(9_000L) {
            val remote = ensureConnected(context.applicationContext) ?: return@withTimeoutOrNull false
            runCatching { remote.playerApi.play(uri) }.isSuccess
        } ?: false
    }

    private suspend fun ensureConnected(context: Context): SpotifyAppRemote? {
        appRemote?.let { if (it.isConnected) return it }
        val params = ConnectionParams.Builder(BuildConfig.SPOTIFY_CLIENT_ID)
            .setRedirectUri(BuildConfig.SPOTIFY_REDIRECT_URI)
            .showAuthView(true)
            .build()
        val remote = suspendCancellableCoroutine { c ->
            SpotifyAppRemote.connect(context, params, object : Connector.ConnectionListener {
                override fun onConnected(remote: SpotifyAppRemote) { if (c.isActive) c.resume(remote) }
                override fun onFailure(error: Throwable) {
                    Log.w("ATLAS", "Spotify App Remote failed: ${error.message}")
                    if (c.isActive) c.resume(null)
                }
            })
        }
        appRemote = remote
        return remote
    }

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")
}
