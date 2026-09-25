package tech.xiaoniu.mooquant

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import tech.xiaoniu.mooquant.data.ApiClient
import tech.xiaoniu.mooquant.data.Prefs
import tech.xiaoniu.mooquant.data.SearchItem

class AddStockActivity : AppCompatActivity() {

    companion object {
        private const val DEBOUNCE_MS = 300L
    }

    private lateinit var prefs: Prefs
    private lateinit var api: ApiClient
    private lateinit var hint: TextView
    private lateinit var adapter: ResultAdapter

    /** 输入防抖：新输入到来时取消上一次待发的搜索 */
    private var searchJob: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_add_stock)
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.addRoot)) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

        prefs = Prefs(this)
        api = ApiClient(prefs)

        findViewById<MaterialToolbar>(R.id.addToolbar).setNavigationOnClickListener { finish() }

        hint = findViewById(R.id.resultHint)
        adapter = ResultAdapter { item -> add(item) }

        val list = findViewById<RecyclerView>(R.id.resultList)
        list.layoutManager = LinearLayoutManager(this)
        list.adapter = adapter

        val input = findViewById<TextInputEditText>(R.id.searchInput)
        input.doAfterTextChanged { text -> onQueryChanged(text?.toString().orEmpty()) }
        input.requestFocus()
    }

    private fun onQueryChanged(raw: String) {
        searchJob?.cancel()

        if (raw.isBlank()) {
            adapter.submit(emptyList())
            showHint(getString(R.string.add_empty))
            return
        }

        searchJob = lifecycleScope.launch {
            delay(DEBOUNCE_MS)
            api.search(raw.trim())
                .onSuccess { items ->
                    adapter.submit(items)
                    if (items.isEmpty()) {
                        showHint(getString(R.string.add_no_result))
                    } else {
                        hint.visibility = View.GONE
                    }
                }
                .onFailure { e ->
                    adapter.submit(emptyList())
                    showHint(getString(R.string.add_search_failed, e.message ?: "未知错误"))
                }
        }
    }

    private fun add(item: SearchItem) {
        if (prefs.addToWatchlist(item.code)) {
            Toast.makeText(this, getString(R.string.add_added, item.name), Toast.LENGTH_SHORT).show()
            finish()
        } else {
            Toast.makeText(this, getString(R.string.add_duplicate, item.name), Toast.LENGTH_SHORT).show()
        }
    }

    private fun showHint(text: String) {
        hint.text = text
        hint.visibility = View.VISIBLE
    }

    private class ResultAdapter(
        private val onClick: (SearchItem) -> Unit,
    ) : RecyclerView.Adapter<ResultAdapter.VH>() {

        private val items = mutableListOf<SearchItem>()

        fun submit(list: List<SearchItem>) {
            items.clear()
            items.addAll(list)
            notifyDataSetChanged()
        }

        class VH(view: View) : RecyclerView.ViewHolder(view) {
            val name: TextView = view.findViewById(R.id.resultName)
            val code: TextView = view.findViewById(R.id.resultCode)
            val industry: TextView = view.findViewById(R.id.resultIndustry)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
            val view = LayoutInflater.from(parent.context)
                .inflate(R.layout.item_search_result, parent, false)
            val holder = VH(view)
            holder.itemView.setOnClickListener {
                val pos = holder.bindingAdapterPosition
                if (pos != RecyclerView.NO_POSITION) onClick(items[pos])
            }
            return holder
        }

        override fun getItemCount(): Int = items.size

        override fun onBindViewHolder(holder: VH, position: Int) {
            val item = items[position]
            holder.name.text = item.name.ifBlank { item.code }
            holder.code.text = item.code
            holder.industry.text = item.industry
        }
    }
}
