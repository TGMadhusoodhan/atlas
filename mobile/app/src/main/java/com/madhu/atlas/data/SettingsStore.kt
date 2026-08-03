package com.madhu.atlas.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "atlas_settings")

/**
 * User-facing preferences. Privacy-first defaults: the app runs fully on-device; the
 * user opts in to letting DeepSeek handle requests when online. [appLockEnabled]
 * gates the app behind biometrics.
 */
class SettingsStore(context: Context) {

    private val store = context.applicationContext.dataStore

    val onlineEnabled: Flow<Boolean> = store.data.map { it[ONLINE] ?: true }
    val appLockEnabled: Flow<Boolean> = store.data.map { it[APP_LOCK] ?: false }

    /** Keep the "Hey Atlas" wake service running in the background (default on). */
    val alwaysListening: Flow<Boolean> = store.data.map { it[ALWAYS_LISTENING] ?: true }

    suspend fun onlineEnabledNow(): Boolean = onlineEnabled.first()

    suspend fun alwaysListeningNow(): Boolean = alwaysListening.first()

    suspend fun setOnlineEnabled(value: Boolean) {
        store.edit { it[ONLINE] = value }
    }

    suspend fun setAppLockEnabled(value: Boolean) {
        store.edit { it[APP_LOCK] = value }
    }

    suspend fun setAlwaysListening(value: Boolean) {
        store.edit { it[ALWAYS_LISTENING] = value }
    }

    private companion object {
        val ONLINE = booleanPreferencesKey("online_enabled")
        val APP_LOCK = booleanPreferencesKey("app_lock_enabled")
        val ALWAYS_LISTENING = booleanPreferencesKey("always_listening")
    }
}
