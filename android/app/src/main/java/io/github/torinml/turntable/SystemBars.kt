package io.github.torinml.turntable

import android.app.Activity
import android.view.View
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat

/** Android 15 draws apps edge to edge, under the status and navigation bars: keep a view's content
 *  clear of them (and of any camera cutout), with light icons on the dark bars. */
object SystemBars {
    fun fit(activity: Activity, view: View, extra: Int = 0) {
        WindowCompat.getInsetsController(activity.window, activity.window.decorView).apply {
            isAppearanceLightStatusBars = false
            isAppearanceLightNavigationBars = false
        }
        ViewCompat.setOnApplyWindowInsetsListener(view) { v, insets ->
            val b = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            v.setPadding(b.left + extra, b.top + extra, b.right + extra, b.bottom + extra)
            WindowInsetsCompat.CONSUMED
        }
        ViewCompat.requestApplyInsets(view)
    }
}
