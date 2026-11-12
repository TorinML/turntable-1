package io.github.torinml.turntable

import android.graphics.Typeface
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

/** Asks for the server's address (works with a touchscreen or a TV remote). */
class SetupActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val gold = ContextCompat.getColor(this, R.color.gold)
        val text = ContextCompat.getColor(this, R.color.text)
        val muted = ContextCompat.getColor(this, R.color.muted)
        val pad = (24 * resources.displayMetrics.density).toInt()

        val title = TextView(this).apply {
            setText(R.string.setup_title); setTextColor(gold); textSize = 26f; setTypeface(typeface, Typeface.BOLD)
        }
        val note = TextView(this).apply {
            setText(R.string.setup_note); setTextColor(muted); textSize = 15f; setPadding(0, pad / 2, 0, pad / 2)
        }
        val input = EditText(this).apply {
            setHint(R.string.setup_hint); setTextColor(text); setHintTextColor(muted); textSize = 20f; isSingleLine = true
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            imeOptions = EditorInfo.IME_ACTION_DONE
            setText(ServerConfig.get(this@SetupActivity) ?: "")
        }
        val error = TextView(this).apply { setTextColor(0xFFFF7A66.toInt()); textSize = 14f }
        val save = Button(this).apply { setText(R.string.setup_save) }

        val submit = {
            val url = ServerConfig.normalize(input.text.toString())
            if (url == null) error.setText(R.string.setup_bad)
            else { ServerConfig.set(this, url); finish() }
        }
        save.setOnClickListener { submit() }
        input.setOnEditorActionListener { _, action, _ -> if (action == EditorInfo.IME_ACTION_DONE) { submit(); true } else false }

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            addView(title); addView(note); addView(input); addView(error); addView(save)
        }
        setContentView(root)
        SystemBars.fit(this, root, extra = pad)
        input.requestFocus()
    }
}
