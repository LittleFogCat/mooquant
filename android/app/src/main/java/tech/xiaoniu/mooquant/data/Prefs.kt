package tech.xiaoniu.mooquant.data

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit
import org.json.JSONArray

/** 本地持久化：服务器地址、token、自选列表、配色方案、刷新间隔。 */
class Prefs(context: Context) {

    companion object {
        private const val FILE = "mooquant"
        private const val KEY_API_BASE = "api_base"
        private const val KEY_TOKEN = "token"
        private const val KEY_WATCHLIST = "watchlist"
        private const val KEY_COLOR_SCHEME = "color_scheme"
        private const val KEY_REFRESH_MS = "refresh_ms"

        const val RED_UP = "red_up"
        const val GREEN_UP = "green_up"

        const val DEFAULT_REFRESH_MS = 3000L

        /** 首次启动的预置自选，免得首屏是空的 */
        val DEFAULT_WATCHLIST = listOf("sh600519", "sz000001", "sh601318")
    }

    private val sp: SharedPreferences = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    var apiBase: String
        get() = sp.getString(KEY_API_BASE, "") ?: ""
        set(v) = sp.edit { putString(KEY_API_BASE, v.trim().trimEnd('/')) }

    var token: String
        get() = sp.getString(KEY_TOKEN, "") ?: ""
        set(v) = sp.edit { putString(KEY_TOKEN, v.trim()) }

    /** 红涨绿跌（A 股习惯，默认）或绿涨红跌 */
    var colorScheme: String
        get() = sp.getString(KEY_COLOR_SCHEME, RED_UP) ?: RED_UP
        set(v) = sp.edit { putString(KEY_COLOR_SCHEME, v) }

    var refreshMs: Long
        get() = sp.getLong(KEY_REFRESH_MS, DEFAULT_REFRESH_MS)
        set(v) = sp.edit { putLong(KEY_REFRESH_MS, v) }

    /** 首次启动给几个常见标的，免得首屏是空的 */
    var watchlist: List<String>
        get() {
            val raw = sp.getString(KEY_WATCHLIST, null) ?: return DEFAULT_WATCHLIST
            return try {
                val arr = JSONArray(raw)
                (0 until arr.length()).map { arr.getString(it) }
            } catch (e: Exception) {
                DEFAULT_WATCHLIST
            }
        }
        set(v) {
            val arr = JSONArray()
            v.forEach { arr.put(it) }
            sp.edit { putString(KEY_WATCHLIST, arr.toString()) }
        }

    /** 加入自选；已存在则不重复。返回 true 表示确实新增了。 */
    fun addToWatchlist(code: String): Boolean {
        val cur = watchlist
        if (cur.contains(code)) return false
        watchlist = cur + code
        return true
    }

    fun removeFromWatchlist(code: String) {
        watchlist = watchlist.filter { it != code }
    }
}
