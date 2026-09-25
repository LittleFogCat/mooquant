package tech.xiaoniu.mooquant

import android.os.Bundle
import android.widget.RadioGroup
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.lifecycleScope
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.launch
import tech.xiaoniu.mooquant.data.ApiClient
import tech.xiaoniu.mooquant.data.Discovery
import tech.xiaoniu.mooquant.data.Prefs

class SettingsActivity : AppCompatActivity() {

    private lateinit var prefs: Prefs
    private lateinit var api: ApiClient

    private lateinit var apiBaseInput: TextInputEditText
    private lateinit var tokenInput: TextInputEditText
    private lateinit var discoverBtn: MaterialButton
    private lateinit var testBtn: MaterialButton
    private lateinit var colorGroup: RadioGroup
    private lateinit var intervalGroup: RadioGroup

    /** 正在把状态写回控件，此时忽略控件回调，避免覆盖用户输入 */
    private var binding = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_settings)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.settingsRoot)) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

        prefs = Prefs(this)
        api = ApiClient(prefs)

        apiBaseInput = findViewById(R.id.apiBaseInput)
        tokenInput = findViewById(R.id.tokenInput)
        discoverBtn = findViewById(R.id.discoverBtn)
        testBtn = findViewById(R.id.testBtn)
        colorGroup = findViewById(R.id.colorGroup)
        intervalGroup = findViewById(R.id.intervalGroup)

        findViewById<MaterialToolbar>(R.id.settingsToolbar)
            .setNavigationOnClickListener { finish() }

        loadFromPrefs()
        wireListeners()
    }

    private fun loadFromPrefs() {
        binding = true

        apiBaseInput.setText(prefs.apiBase)
        tokenInput.setText(prefs.token)
        colorGroup.check(
            if (prefs.colorScheme == Prefs.GREEN_UP) R.id.colorGreenUp else R.id.colorRedUp
        )
        intervalGroup.check(
            when (prefs.refreshMs) {
                1000L -> R.id.interval1s
                5000L -> R.id.interval5s
                10000L -> R.id.interval10s
                else -> R.id.interval3s
            }
        )

        binding = false
    }

    private fun wireListeners() {
        // 地址与 token 即时落盘；地址在失焦时才校验，避免打字过程中反复 trim
        apiBaseInput.doAfterTextChanged { text ->
            if (binding) return@doAfterTextChanged
            prefs.apiBase = text?.toString().orEmpty()
        }
        tokenInput.doAfterTextChanged { text ->
            if (binding) return@doAfterTextChanged
            prefs.token = text?.toString().orEmpty()
        }

        colorGroup.setOnCheckedChangeListener { _, checkedId ->
            if (binding) return@setOnCheckedChangeListener
            prefs.colorScheme = if (checkedId == R.id.colorGreenUp) Prefs.GREEN_UP else Prefs.RED_UP
        }

        intervalGroup.setOnCheckedChangeListener { _, checkedId ->
            if (binding) return@setOnCheckedChangeListener
            prefs.refreshMs = when (checkedId) {
                R.id.interval1s -> 1000L
                R.id.interval5s -> 5000L
                R.id.interval10s -> 10000L
                else -> 3000L
            }
        }

        discoverBtn.setOnClickListener { runDiscovery() }
        testBtn.setOnClickListener { runTest() }

        findViewById<MaterialButton>(R.id.resetWatchlistBtn).setOnClickListener {
            prefs.watchlist = Prefs.DEFAULT_WATCHLIST
            Toast.makeText(this, getString(R.string.settings_reset_done), Toast.LENGTH_SHORT).show()
        }
    }

    private fun runDiscovery() {
        setBusy(discoverBtn, true, R.string.settings_discovering)
        lifecycleScope.launch {
            val target = Discovery(this@SettingsActivity).discover()
            setBusy(discoverBtn, false, R.string.settings_discover)
            if (target == null) {
                toast(getString(R.string.settings_discover_fail))
                return@launch
            }
            binding = true
            apiBaseInput.setText(target.apiBase)
            binding = false
            prefs.apiBase = target.apiBase
            toast(getString(R.string.settings_discover_ok, target.apiBase))
        }
    }

    private fun runTest() {
        setBusy(testBtn, true, R.string.settings_testing)
        lifecycleScope.launch {
            val result = api.testConnection()
            setBusy(testBtn, false, R.string.settings_test)
            result
                .onSuccess { source ->
                    val name = when (source) {
                        "qmt" -> getString(R.string.source_qmt)
                        "mock" -> getString(R.string.source_mock)
                        else -> source.ifBlank { "unknown" }
                    }
                    toast(getString(R.string.settings_test_ok, name))
                }
                .onFailure { e ->
                    toast(getString(R.string.settings_test_fail, e.message ?: "未知错误"))
                }
        }
    }

    private fun setBusy(button: MaterialButton, busy: Boolean, labelRes: Int) {
        button.isEnabled = !busy
        button.setText(labelRes)
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }
}
