package tech.xiaoniu.mooquant.data

/**
 * 行情快照。/mooquant/api/quotes 的 quotes[] 元素（服务端 shapeQuote 后的字段子集）。
 * [missing] = 服务端没返回这个标的（代码失效 / 数据源缺失），列表里占位显示而不是静默消失。
 */
data class Quote(
    val code: String,
    val name: String,
    val price: Double,
    val change: Double,
    val changePercent: Double,
    val prevClose: Double,
    val missing: Boolean = false,
)

/** 搜索结果。/mooquant/api/search 返回的裸数组元素。 */
data class SearchItem(
    val code: String,
    val name: String,
    val industry: String,
)

/** 发现到的服务端。apiBase 形如 http://192.168.1.5:12678/mooquant/api */
data class ServerTarget(
    val apiBase: String,
    val source: String,
    val tokenRequired: Boolean,
)
