package io.github.torinml.turntable

import android.content.Context
import java.net.URL

/** Where the Turntable-1 server is: set on first launch, changeable later. */
object ServerConfig {
    private const val PREFS = "turntable"
    private const val KEY = "server"

    fun get(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null)

    fun set(context: Context, url: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, url).apply()
    }

    /** "192.168.0.20:8080" becomes "http://192.168.0.20:8080"; a trailing slash is dropped. Null if unusable. */
    fun normalize(input: String): String? {
        var u = input.trim().trimEnd('/')
        if (u.isEmpty()) return null
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://$u"
        return try {
            val url = URL(u)
            if (url.host.isNullOrEmpty()) null else u
        } catch (e: Exception) {
            null
        }
    }
}
