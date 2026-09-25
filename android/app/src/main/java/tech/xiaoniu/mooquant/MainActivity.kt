package tech.xiaoniu.mooquant

import android.content.Intent
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.widget.TextView
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.appbar.MaterialToolbar
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import tech.xiaoniu.mooquant.data.ApiClient
import tech.xiaoniu.mooquant.data.Discovery
import tech.xiaoniu.mooquant.data.Prefs
import tech.xiaoniu.mooquant.data.Quote
import tech.xiaoniu.mooquant.ui.QuoteAdapter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var prefs: Prefs
    private lateinit var api: ApiClient
    private lateinit var adapter: QuoteAdapter

    private lateinit var statusBar: TextView
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var emptyView: TextView

    private var lastError: String? = null
    private var lastUpdatedAt: Long = 0L

    /** 发现到的服务端声明需要 token —— 本地没填时在状态条提示 */
    private var needsToken = false

    /** 正在自动发现。期间刷新循环让位，否则状态条会被空地址的结果覆盖成「未发现服务器」 */
    private var discovering = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }

        prefs = Prefs(this)
        api = ApiClient(prefs)

        setSupportActionBar(findViewById<MaterialToolbar>(R.id.toolbar))

        statusBar = findViewById(R.id.statusBar)
        swipe = findViewById(R.id.swipe)
        emptyView = findViewById(R.id.emptyView)

        adapter = QuoteAdapter(upColor(), downColor(), flatColor()) { quote -> confirmDelete(quote) }
        val list = findViewById<RecyclerView>(R.id.quoteList)
        list.layoutManager = LinearLayoutManager(this)
        list.adapter = adapter

        swipe.setOnRefreshListener { lifecycleScope.launch { refresh() } }
        statusBar.setOnClickListener { openSettings() }

        // 首次启动还没有服务器地址：先自动发现，失败则引导去设置页。
        // 这个标志在启动协程之前同步落定，两个协程之间不存在竞态。
        discovering = prefs.apiBase.isEmpty()
        if (discovering) renderEmpty(true, noServer = true) // 发现期间先别把空列表摊出来
        renderStatus()

        lifecycleScope.launch {
            if (discovering) {
                val target = Discovery(this@MainActivity).discover()
                if (target != null) {
                    prefs.apiBase = target.apiBase
                    needsToken = target.tokenRequired
                }
                discovering = false
            }
            refresh()
        }

        // 自动刷新：随页面可见性启停；用 delay 循环而非固定频率，天然不会请求重叠
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    if (!discovering) refresh()
                    delay(prefs.refreshMs)
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // 设置页可能改了配色；自选则在每次 refresh 时从 Prefs 重新读取
        adapter.setColors(upColor(), downColor(), flatColor())
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.menu_main, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        R.id.action_refresh -> {
            swipe.isRefreshing = true
            lifecycleScope.launch { refresh() }
            true
        }

        R.id.action_add -> {
            startActivity(Intent(this, AddStockActivity::class.java))
            true
        }

        R.id.action_settings -> {
            openSettings()
            true
        }

        else -> super.onOptionsItemSelected(item)
    }

    // ---- 数据 ----

    private suspend fun refresh() {
        val codes = prefs.watchlist

        if (prefs.apiBase.isEmpty()) {
            adapter.submit(emptyList())
            renderEmpty(true, noServer = true)
            renderStatus()
            swipe.isRefreshing = false
            return
        }

        if (codes.isEmpty()) {
            adapter.submit(emptyList())
            renderEmpty(true)
            lastError = null
            renderStatus()
            swipe.isRefreshing = false
            return
        }

        api.quotes(codes)
            .onSuccess { quotes ->
                adapter.submit(orderByWatchlist(codes, quotes))
                renderEmpty(false)
                lastError = null
                lastUpdatedAt = System.currentTimeMillis()
            }
            .onFailure { e ->
                // 保留旧数据，只在状态条上报错，避免闪白
                lastError = e.message ?: "请求失败"
            }

        renderStatus()
        swipe.isRefreshing = false
    }

    /**
     * 按自选顺序排列；服务端没返回的（代码失效 / 数据源缺失）用占位项补上，
     * 否则它会从列表里静默消失，看不出是哪里不对。
     */
    private fun orderByWatchlist(codes: List<String>, quotes: List<Quote>): List<Quote> {
        val byCode = quotes.associateBy { it.code }
        return codes.map { code ->
            byCode[code] ?: Quote(
                code = code,
                name = code,
                price = 0.0,
                change = 0.0,
                changePercent = 0.0,
                prevClose = 0.0,
                missing = true,
            )
        }
    }

    // ---- 视图 ----

    /**
     * 两种「空」的成因不同，文案不能混：没配服务器 ≠ 自选为空。
     * 后者会让已经配好自选的人以为自己的列表丢了。
     */
    private fun renderEmpty(empty: Boolean, noServer: Boolean = false) {
        emptyView.text =
            getString(if (noServer) R.string.empty_no_server else R.string.empty_watchlist)
        emptyView.visibility = if (empty) View.VISIBLE else View.GONE
        swipe.visibility = if (empty) View.GONE else View.VISIBLE
    }

    private fun renderStatus() {
        if (discovering) {
            statusBar.text = getString(R.string.status_discovering)
            return
        }

        val base = prefs.apiBase
        if (base.isEmpty()) {
            statusBar.text = getString(R.string.status_not_configured)
            return
        }

        val host = base.removePrefix("http://").removePrefix("https://").substringBefore('/')
        statusBar.text = when {
            lastError != null -> "$host · $lastError"

            lastUpdatedAt == 0L -> "$host · ${getString(R.string.status_refreshing)}"

            else -> {
                val src = when (api.lastSource) {
                    "qmt" -> getString(R.string.source_qmt)
                    "mock" -> getString(R.string.source_mock)
                    else -> ""
                }
                val time = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date(lastUpdatedAt))
                buildString {
                    append(host)
                    if (src.isNotEmpty()) append(" · ").append(src)
                    if (needsToken && prefs.token.isEmpty()) {
                        append(" · ").append(getString(R.string.token_required_hint))
                    }
                    append(" · ").append(getString(R.string.status_updated, time))
                }
            }
        }
    }

    private fun confirmDelete(quote: Quote) {
        AlertDialog.Builder(this)
            .setTitle(R.string.delete_title)
            .setMessage(getString(R.string.delete_message, quote.name.ifBlank { quote.code }))
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.delete_confirm) { _, _ ->
                prefs.removeFromWatchlist(quote.code)
                lifecycleScope.launch { refresh() }
            }
            .show()
    }

    private fun openSettings() {
        startActivity(Intent(this, SettingsActivity::class.java))
    }

    private fun upColor(): Int = ContextCompat.getColor(
        this, if (prefs.colorScheme == Prefs.GREEN_UP) R.color.quote_down_green else R.color.quote_up_red
    )

    private fun downColor(): Int = ContextCompat.getColor(
        this, if (prefs.colorScheme == Prefs.GREEN_UP) R.color.quote_up_red else R.color.quote_down_green
    )

    private fun flatColor(): Int = ContextCompat.getColor(this, R.color.quote_flat)
}
