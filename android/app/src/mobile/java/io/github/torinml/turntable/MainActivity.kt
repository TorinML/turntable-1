package io.github.torinml.turntable

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.widget.FrameLayout
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.MoreExecutors
import org.json.JSONObject

/** The phone and tablet app: the whole site, with its audio (Listen live, and recorded sessions and
 *  albums in the floating player) played by the app's own player, so it keeps going in the
 *  background with notification and lock-screen controls. */
class MainActivity : AppCompatActivity() {
    private lateinit var web: WebView
    private var loaded: String? = null
    private val handler = Handler(Looper.getMainLooper())
    private var controllerFuture: ListenableFuture<MediaController>? = null
    private var controller: MediaController? = null
    private var wantListening = false
    private var pendingQueue: String? = null
    private var deckMode = false

    private val back = object : OnBackPressedCallback(true) {
        override fun handleOnBackPressed() {
            if (web.canGoBack()) web.goBack() else { isEnabled = false; onBackPressedDispatcher.onBackPressed() }
        }
    }
    private val retry = Runnable { loaded?.let { web.loadUrl("$it/") } }
    private val deckReporter = object : Runnable {
        override fun run() {
            reportDeck()
            if (deckMode) handler.postDelayed(this, 500)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        // the page sits in a frame kept clear of the status and navigation bars (Android 15 is edge to edge)
        val root = FrameLayout(this).apply {
            setBackgroundColor(0xFF070B16.toInt())
            addView(web, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        }
        setContentView(root)
        SystemBars.fit(this, root)
        WebViews.configure(this, web, autoplay = false) { retrySoon() }
        web.addJavascriptInterface(Bridge(), "TurntableApp")
        onBackPressedDispatcher.addCallback(this, back)

        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    override fun onStart() {
        super.onStart()
        val token = SessionToken(this, ComponentName(this, PlaybackService::class.java))
        val future = MediaController.Builder(this, token).buildAsync()
        controllerFuture = future
        future.addListener({
            controller = try { future.get() } catch (e: Exception) { null }
            controller?.addListener(playerListener)
            pendingQueue?.let { pendingQueue = null; setDeckQueue(it) }
            if (wantListening) startListening()
            reportListening(null)
            if (deckMode) { handler.removeCallbacks(deckReporter); handler.post(deckReporter) }
        }, MoreExecutors.directExecutor())
    }

    override fun onResume() {
        super.onResume()
        back.isEnabled = true
        val server = ServerConfig.get(this)
        if (server == null) {
            startActivity(Intent(this, SetupActivity::class.java))
            return
        }
        if (server != loaded) {
            loaded = server
            web.loadUrl("$server/")
        }
    }

    override fun onStop() {
        handler.removeCallbacks(deckReporter)
        controller?.removeListener(playerListener)
        controllerFuture?.let { MediaController.releaseFuture(it) }     // the service keeps playing on its own
        controller = null
        controllerFuture = null
        super.onStop()
    }

    /** The server couldn't be reached: try again in a few seconds. */
    private fun retrySoon() {
        handler.removeCallbacks(retry)
        handler.postDelayed(retry, 8000)
    }

    private fun js(code: String) = runOnUiThread { web.evaluateJavascript(code, null) }

    private fun metadata(title: String, artist: String, album: String, cover: String): MediaMetadata =
        MediaMetadata.Builder()
            .setTitle(title.ifEmpty { "Turntable-1" })
            .setArtist(artist.ifEmpty { null })
            .setAlbumTitle(album.ifEmpty { null })
            .setArtworkUri(if (cover.isNotEmpty()) Uri.parse(cover) else null)
            .build()

    // --- Listen live -----------------------------------------------------------------------------

    private fun startListening() {
        val server = loaded ?: return
        val c = controller ?: run { wantListening = true; return }
        wantListening = false
        stopDeckReports()
        val item = MediaItem.Builder()
            .setMediaId(LIVE)
            .setUri("$server/stream?t=${System.currentTimeMillis()}")     // a fresh URL joins the live stream
            .setMediaMetadata(metadata("Turntable-1", "Live from the turntable", "", ""))
            .build()
        c.setMediaItem(item)
        c.prepare()
        c.play()
    }

    private fun stopListening() {
        wantListening = false
        val c = controller
        if (c != null && c.currentMediaItem?.mediaId == LIVE) { c.stop(); c.clearMediaItems() }
        reportListening(null)
    }

    /** Keep the page's Listen button in step with the player (including the notification's controls). */
    private fun reportListening(message: String?) {
        val c = controller
        val on = c != null && c.currentMediaItem?.mediaId == LIVE && c.playWhenReady &&
            c.playbackState != Player.STATE_IDLE && c.playbackState != Player.STATE_ENDED
        val msg = if (message == null) "null" else JSONObject.quote(message)
        js("window.turntableAppListening && window.turntableAppListening($on, $msg)")
    }

    // --- The floating player: recorded sessions and albums ---------------------------------------

    private fun setDeckQueue(json: String) {
        val c = controller ?: run { pendingQueue = json; return }
        val o = JSONObject(json)
        val label = o.optString("label")
        val parts = o.getJSONArray("parts")
        val items = (0 until parts.length()).map { k ->
            val p = parts.getJSONObject(k)
            MediaItem.Builder()
                .setMediaId("$DECK$k")
                .setUri(p.getString("url"))
                .setMediaMetadata(metadata(p.optString("title", label), p.optString("artist"), p.optString("album"), p.optString("cover")))
                .build()
        }
        c.playWhenReady = false
        c.setMediaItems(items)
        c.prepare()
        deckMode = true
        reportListening(null)                          // live listening, if it was on, has been replaced
        handler.removeCallbacks(deckReporter)
        handler.post(deckReporter)
    }

    private fun onDeck(): MediaController? = controller?.takeIf { it.currentMediaItem?.mediaId?.startsWith(DECK) == true }

    private fun stopDeckReports() {
        deckMode = false
        handler.removeCallbacks(deckReporter)
    }

    private fun reportDeck() {
        val c = onDeck() ?: return
        val playing = c.isPlaying || (c.playWhenReady && c.playbackState == Player.STATE_BUFFERING)
        val s = JSONObject()
            .put("i", c.currentMediaItemIndex)
            .put("t", c.currentPosition / 1000.0)
            .put("playing", playing)
            .put("ended", c.playbackState == Player.STATE_ENDED)
        js("window.turntableAppDeck && window.turntableAppDeck($s)")
    }

    private val playerListener = object : Player.Listener {
        override fun onEvents(player: Player, events: Player.Events) {
            if (events.containsAny(Player.EVENT_PLAY_WHEN_READY_CHANGED, Player.EVENT_PLAYBACK_STATE_CHANGED,
                    Player.EVENT_MEDIA_ITEM_TRANSITION)) {
                reportListening(null)
                if (deckMode) reportDeck()
            }
        }

        override fun onPlayerError(error: PlaybackException) {
            val c = controller ?: return
            if (c.currentMediaItem?.mediaId == LIVE) {
                c.stop(); c.clearMediaItems()
                reportListening("The stream stopped. Check that the turntable is switched on, then try again.")
            } else {
                js("window.turntableAppDeck && window.turntableAppDeck({\"i\": ${c.currentMediaItemIndex}, \"t\": 0, \"playing\": false, \"error\": true})")
            }
        }
    }

    /** What the page can ask of the app. */
    inner class Bridge {
        @JavascriptInterface fun listen(on: Boolean) = runOnUiThread { if (on) startListening() else stopListening() }
        @JavascriptInterface fun deckQueue(json: String) = runOnUiThread { setDeckQueue(json) }
        @JavascriptInterface fun deckSelect(index: Int) = runOnUiThread { controller?.seekTo(index, 0) }
        @JavascriptInterface fun deckSeek(ms: Long) = runOnUiThread { onDeck()?.seekTo(ms) }
        @JavascriptInterface fun deckPlay() = runOnUiThread { onDeck()?.play() }
        @JavascriptInterface fun deckPause() = runOnUiThread { onDeck()?.pause() }
        @JavascriptInterface fun deckStop() = runOnUiThread {
            stopDeckReports()
            onDeck()?.run { stop(); clearMediaItems() }
        }
        @JavascriptInterface fun deckMeta(json: String) = runOnUiThread {
            val c = onDeck() ?: return@runOnUiThread
            val item = c.currentMediaItem ?: return@runOnUiThread
            val o = JSONObject(json)
            val updated = item.buildUpon()
                .setMediaMetadata(metadata(o.optString("title"), o.optString("artist"), o.optString("album"), o.optString("cover")))
                .build()
            c.replaceMediaItem(c.currentMediaItemIndex, updated)       // same audio: playback carries on
        }
    }

    companion object {
        private const val LIVE = "live"
        private const val DECK = "deck:"
    }
}
