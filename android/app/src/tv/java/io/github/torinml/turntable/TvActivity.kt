package io.github.torinml.turntable

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.view.WindowManager
import android.webkit.WebView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat

/** The TV app: the TV view, full screen, with the stream playing. The remote's Back opens a menu. */
class TvActivity : AppCompatActivity() {
    private lateinit var web: WebView
    private var loaded: String? = null
    private val handler = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
        web = WebView(this)
        setContentView(web)
        WebViews.configure(this, web, autoplay = true, wideLayout = false) { retrySoon() }
    }

    override fun onResume() {
        super.onResume()
        val server = ServerConfig.get(this)
        if (server == null) {
            startActivity(Intent(this, SetupActivity::class.java))
            return
        }
        if (server != loaded) load(server)
    }

    override fun onStop() {
        // leaving the app (Home, or another app): stop the music; it reloads when you come back
        handler.removeCallbacksAndMessages(null)
        web.loadUrl("about:blank")
        loaded = null
        super.onStop()
    }

    private fun load(server: String) {
        loaded = server
        web.loadUrl("$server/static/tv.html?audio=1")
    }

    /** The server couldn't be reached: try again in a few seconds. */
    private fun retrySoon() {
        handler.removeCallbacksAndMessages(null)
        handler.postDelayed({ loaded?.let { load(it) } }, 8000)
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        when (event.keyCode) {
            KeyEvent.KEYCODE_BACK -> {
                // The page steps back first (out of an overlay, a tab's content, a tab); at Now it
                // answers false, and the app shows its menu.
                if (event.action == KeyEvent.ACTION_UP) {
                    web.evaluateJavascript("(window.tvBack ? window.tvBack() : false)") { handled ->
                        if (handled != "true") showMenu()
                    }
                }
                return true
            }
            KeyEvent.KEYCODE_MENU -> {
                if (event.action == KeyEvent.ACTION_UP) showMenu()
                return true
            }
        }
        return super.dispatchKeyEvent(event)
    }

    private fun showMenu() {
        val items = arrayOf(getString(R.string.tv_reload), getString(R.string.tv_change), getString(R.string.tv_exit))
        AlertDialog.Builder(this)
            .setTitle(R.string.tv_menu_title)
            .setItems(items) { _, which ->
                when (which) {
                    0 -> ServerConfig.get(this)?.let { load(it) }
                    1 -> { loaded = null; startActivity(Intent(this, SetupActivity::class.java)) }
                    2 -> finish()
                }
            }
            .show()
    }
}
