package com.madhu.atlas.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Encrypted, on-device storage for secrets (currently just the DeepSeek API key).
 * Backed by the Android Keystore via EncryptedSharedPreferences — the key never
 * leaves the device and is not in plaintext on disk.
 */
class Secrets(context: Context) {

    private val prefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(context.applicationContext)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context.applicationContext,
            "atlas_secrets",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    var deepSeekApiKey: String?
        get() = prefs.getString(KEY_DEEPSEEK, null)?.takeIf { it.isNotBlank() }
        set(value) = prefs.edit().apply {
            if (value.isNullOrBlank()) remove(KEY_DEEPSEEK) else putString(KEY_DEEPSEEK, value.trim())
        }.apply()

    private companion object {
        const val KEY_DEEPSEEK = "deepseek_api_key"
    }
}
