// Top-level build file. Plugin versions are declared here with `apply false`
// and applied in the module build scripts.
plugins {
    id("com.android.application") version "8.7.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21" apply false
    id("com.google.devtools.ksp") version "2.0.21-1.0.25" apply false
    id("io.objectbox") version "4.0.3" apply false
}
