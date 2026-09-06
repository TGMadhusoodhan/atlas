import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

// Spotify credentials come from local.properties (gitignored) — never committed.
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
val spotifyClientId: String = localProps.getProperty("spotify.clientId", "")

android {
    namespace = "com.madhu.atlas"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.madhu.atlas"
        minSdk = 31          // Android 12; broad Snapdragon coverage
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-m1"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Spotify App Remote config (blank → SDK path skipped, intent fallback used).
        buildConfigField("String", "SPOTIFY_CLIENT_ID", "\"$spotifyClientId\"")
        buildConfigField("String", "SPOTIFY_REDIRECT_URI", "\"com.madhu.atlas://callback\"")
        // Redirect URI split for the Spotify Auth library's manifest receiver.
        manifestPlaceholders["redirectSchemeName"] = "com.madhu.atlas"
        manifestPlaceholders["redirectHostName"] = "callback"

        // The Genie/QNN NDK bridge (M1 step 5) builds for arm64 only — that's the
        // only ABI the phone uses and the only one the QNN runtime ships for here.
        ndk {
            abiFilters += "arm64-v8a"
        }
        externalNativeBuild {
            cmake {
                cppFlags += "-std=c++17"
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
        debug {
            isMinifyEnabled = false
        }
    }

    // The native bridge is added in M1 step 5. The file is present as a stub now;
    // uncomment once the QNN SDK path is configured in local.properties.
    // externalNativeBuild {
    //     cmake {
    //         path = file("src/main/cpp/CMakeLists.txt")
    //         version = "3.22.1"
    //     }
    // }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
        // ONNX Runtime + QNN ship large native libs; keep them uncompressed.
        jniLibs {
            useLegacyPackaging = false
        }
    }
}

dependencies {
    // Compose (BOM keeps versions aligned)
    val composeBom = platform("androidx.compose:compose-bom:2024.10.01")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    // Networking (DeepSeek SSE) + JSON
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")

    // Room — profile facts + semantic-memory vectors (brute-force cosine)
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")

    // Security: biometric app-lock (secret encryption uses the Android Keystore
    // directly in data/Secrets.kt — no Jetpack Security dependency).
    implementation("androidx.biometric:biometric:1.1.0")

    // DataStore for lightweight settings
    implementation("androidx.datastore:datastore-preferences:1.1.1")

    // WorkManager — schedules reminder notifications
    implementation("androidx.work:work-runtime-ktx:2.9.1")

    // Voice (M3): Vosk does both the "Hey Atlas" wake word (grammar keyword-spotting)
    // and offline command STT — no account, nothing leaves the device.
    // (TTS uses the built-in android.speech.tts.TextToSpeech — no dependency.)
    implementation("com.alphacephei:vosk-android:0.3.75")

    // Spotify App Remote (playback) + Auth (account login for library access). The .aars
    // are downloaded into app/libs/ (gitignored) per Spotify's licence; gson is App
    // Remote's runtime dep, androidx.browser is Auth's (Custom Tabs consent screen).
    implementation(files("libs/spotify-app-remote-release-0.8.0.aar"))
    implementation("com.google.code.gson:gson:2.10.1")
    implementation("androidx.browser:browser:1.8.0")

    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.compose.ui:ui-tooling-preview")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
