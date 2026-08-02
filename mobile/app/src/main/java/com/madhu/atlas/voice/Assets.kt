package com.madhu.atlas.voice

import android.content.Context
import java.io.File

/** Small helpers for shipping model/keyword files as assets and using them at runtime. */
object Assets {

    fun exists(context: Context, assetPath: String): Boolean = try {
        context.assets.open(assetPath).close(); true
    } catch (e: Exception) {
        false
    }

    /** Copy an asset file into app storage once, returning the on-disk path (Porcupine
     *  needs a filesystem path for the .ppn keyword). */
    fun copyFile(context: Context, assetPath: String, outName: String): File {
        val out = File(context.filesDir, outName)
        if (!out.exists() || out.length() == 0L) {
            context.assets.open(assetPath).use { input ->
                out.outputStream().use { input.copyTo(it) }
            }
        }
        return out
    }
}
