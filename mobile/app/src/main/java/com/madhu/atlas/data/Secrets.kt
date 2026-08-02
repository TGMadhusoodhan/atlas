package com.madhu.atlas.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Encrypted, on-device storage for secrets (currently the DeepSeek API key).
 *
 * The value is encrypted with an AES-256-GCM key held in the hardware-backed Android
 * Keystore (the key material never leaves the device / secure element) and the resulting
 * ciphertext is kept in plain SharedPreferences. This replaces Jetpack Security's
 * EncryptedSharedPreferences, which is deprecated and known to fail reading back values
 * on later launches — which made the key look "not saved". Decryption fails soft
 * (returns null) so a corrupt/rotated key just means the user re-enters it, never a crash.
 */
class Secrets(context: Context) {

    private val prefs = context.applicationContext
        .getSharedPreferences("atlas_secrets", Context.MODE_PRIVATE)

    var deepSeekApiKey: String?
        get() = decrypt(prefs.getString(KEY_DEEPSEEK, null))?.takeIf { it.isNotBlank() }
        set(value) = put(KEY_DEEPSEEK, value)

    /** Picovoice AccessKey for the "Hey Atlas" wake word (free from console.picovoice.ai). */
    var picovoiceAccessKey: String?
        get() = decrypt(prefs.getString(KEY_PICOVOICE, null))?.takeIf { it.isNotBlank() }
        set(value) = put(KEY_PICOVOICE, value)

    private fun put(key: String, value: String?) {
        prefs.edit().apply {
            if (value.isNullOrBlank()) remove(key) else putString(key, encrypt(value.trim()))
        }.apply()
    }

    // ── AES-256-GCM via Android Keystore ────────────────────────────────────────

    private fun secretKey(): SecretKey {
        val ks = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (ks.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        gen.init(
            KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return gen.generateKey()
    }

    /** Returns Base64( ivLen(1) | iv | ciphertext ). */
    private fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val iv = cipher.iv
        val ct = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        val out = ByteArray(1 + iv.size + ct.size)
        out[0] = iv.size.toByte()
        System.arraycopy(iv, 0, out, 1, iv.size)
        System.arraycopy(ct, 0, out, 1 + iv.size, ct.size)
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    private fun decrypt(stored: String?): String? {
        if (stored.isNullOrBlank()) return null
        return runCatching {
            val data = Base64.decode(stored, Base64.NO_WRAP)
            val ivLen = data[0].toInt()
            val iv = data.copyOfRange(1, 1 + ivLen)
            val ct = data.copyOfRange(1 + ivLen, data.size)
            val cipher = Cipher.getInstance(TRANSFORM)
            cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(128, iv))
            String(cipher.doFinal(ct), Charsets.UTF_8)
        }.getOrNull()
    }

    private companion object {
        const val KEY_DEEPSEEK = "deepseek_api_key"
        const val KEY_PICOVOICE = "picovoice_access_key"
        const val KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "atlas_secret_key"
        const val TRANSFORM = "AES/GCM/NoPadding"
    }
}
