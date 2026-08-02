package com.madhu.atlas.profile

import androidx.room.Entity
import androidx.room.PrimaryKey

/** A durable fact ATLAS has learned about the user (mirrors a bullet in profile.md). */
@Entity(tableName = "profile_facts")
data class ProfileFact(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val category: String = "Other",
    val fact: String = "",
    val createdAt: Long = System.currentTimeMillis(),
)

/** The fixed set of categories, matching the desktop profile sections. */
object ProfileCategories {
    val ALL = listOf("Identity", "Preferences", "Projects", "Environment", "Goals", "Other")
    fun normalise(raw: String?): String {
        val t = raw?.trim()?.replaceFirstChar { it.uppercase() } ?: "Other"
        return if (t in ALL) t else "Other"
    }
}
