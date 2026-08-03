package com.madhu.atlas.tools

import android.content.Context
import android.provider.ContactsContract

/** A resolved contact — a display name and one phone number. */
data class Contact(val name: String, val number: String)

/**
 * Look up phone numbers by (partial) contact name via the Contacts provider.
 * Requires the READ_CONTACTS permission; returns an empty list if it's not granted
 * or nothing matches, so callers can fail soft.
 */
object Contacts {
    fun resolve(context: Context, name: String): List<Contact> {
        val query = name.trim()
        if (query.isEmpty()) return emptyList()
        val resolver = context.applicationContext.contentResolver
        val projection = arrayOf(
            ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            ContactsContract.CommonDataKinds.Phone.NUMBER,
        )
        val selection = "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} LIKE ?"
        val args = arrayOf("%$query%")
        val seen = LinkedHashMap<String, Contact>()   // de-dupe by normalised number
        return runCatching {
            resolver.query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                projection, selection, args,
                "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} ASC",
            )?.use { c ->
                val nameIdx = c.getColumnIndex(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME)
                val numIdx = c.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
                while (c.moveToNext()) {
                    val cName = c.getString(nameIdx)?.trim().orEmpty()
                    val cNum = c.getString(numIdx)?.trim().orEmpty()
                    if (cNum.isEmpty()) continue
                    val key = cNum.filter { it.isDigit() }
                    seen.getOrPut(key) { Contact(cName.ifEmpty { query }, cNum) }
                }
            }
            seen.values.toList()
        }.getOrDefault(emptyList())
    }
}
