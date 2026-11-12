package io.github.torinml.turntable

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity

/** The web view both apps share: the site, with links off-site opening in the browser and a
 *  friendly screen (that retries by itself) when the server can't be reached. */
object WebViews {
    @SuppressLint("SetJavaScriptEnabled")
    fun configure(activity: AppCompatActivity, web: WebView, autoplay: Boolean, wideLayout: Boolean = true, onOffline: () -> Unit) {
        web.setBackgroundColor(0xFF0E1730.toInt())
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = !autoplay
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
            // Lay the page out at the width it asks for (the site's 1260 px), then zoom out to fit
            // the screen, as Chrome does; without these, the web view squeezes it to the phone's width.
            // (the TV view fits itself to the screen, so the TV app leaves these off)
            useWideViewPort = wideLayout
            loadWithOverviewMode = wideLayout
            // pinch to zoom, as in the browser, without on-screen zoom buttons
            setSupportZoom(true)
            builtInZoomControls = true
            displayZoomControls = false
            userAgentString = "$userAgentString Turntable-1-App"
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val server = ServerConfig.get(activity) ?: return false
                val target = request.url
                if (target.host == Uri.parse(server).host) return false           // our own pages stay in the app
                try {
                    activity.startActivity(Intent(Intent.ACTION_VIEW, target))
                } catch (e: ActivityNotFoundException) { /* no browser: stay put */ }
                return true
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) {
                    showOffline(view, ServerConfig.get(activity) ?: "")
                    onOffline()
                }
            }

            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {}
        }
    }

    fun showOffline(web: WebView, server: String) {
        val html = """
            <html><body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
            background:#0E1730;color:#EEF2F6;font-family:sans-serif;text-align:center">
            <div><div style="font-size:28px;font-weight:800;color:#F2B544">Can't reach Turntable-1</div>
            <p style="font-size:17px;color:#9AA8BA;max-width:520px;margin:14px auto">Trying ${server.replace("<", "&lt;")} again every few seconds.
            Check the address and that the Pi is on. To change the address, long-press the app's icon (phone) or press Back (TV).</p></div>
            </body></html>""".trimIndent()
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
    }
}
