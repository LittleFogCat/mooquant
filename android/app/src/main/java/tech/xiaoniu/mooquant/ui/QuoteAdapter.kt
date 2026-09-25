package tech.xiaoniu.mooquant.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import tech.xiaoniu.mooquant.R
import tech.xiaoniu.mooquant.data.Quote
import java.util.Locale

/** 自选行情列表。涨跌颜色由外部传入（配色方案可切换）。 */
class QuoteAdapter(
    private var colorUp: Int,
    private var colorDown: Int,
    private var colorFlat: Int,
    private val onLongClick: (Quote) -> Unit,
) : RecyclerView.Adapter<QuoteAdapter.VH>() {

    private val items = mutableListOf<Quote>()

    /** 传不可变快照进来：适配器只持有自己的副本，不受调用方后续改动影响。 */
    fun submit(list: List<Quote>) {
        items.clear()
        items.addAll(list)
        notifyDataSetChanged()
    }

    fun setColors(up: Int, down: Int, flat: Int) {
        colorUp = up
        colorDown = down
        colorFlat = flat
        notifyDataSetChanged()
    }

    class VH(view: View) : RecyclerView.ViewHolder(view) {
        val name: TextView = view.findViewById(R.id.quoteName)
        val code: TextView = view.findViewById(R.id.quoteCode)
        val price: TextView = view.findViewById(R.id.quotePrice)
        val change: TextView = view.findViewById(R.id.quoteChange)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_quote, parent, false)
        val holder = VH(view)
        holder.itemView.setOnLongClickListener {
            val pos = holder.bindingAdapterPosition
            if (pos != RecyclerView.NO_POSITION) onLongClick(items[pos])
            true
        }
        return holder
    }

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: VH, position: Int) {
        val q = items[position]
        holder.name.text = q.name.ifBlank { q.code }
        holder.code.text = q.code

        if (q.missing) {
            holder.price.text = holder.itemView.context.getString(R.string.quote_placeholder)
            holder.change.text = ""
            holder.price.setTextColor(colorFlat)
            holder.change.setTextColor(colorFlat)
            return
        }

        holder.price.text = String.format(Locale.US, "%.2f", q.price)
        holder.change.text = String.format(
            Locale.US, "%+.2f  %+.2f%%", q.change, q.changePercent
        )

        val color = when {
            q.change > 0 -> colorUp
            q.change < 0 -> colorDown
            else -> colorFlat
        }
        holder.price.setTextColor(color)
        holder.change.setTextColor(color)
    }
}
