package tech.xiaoniu.mooquant.data

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** 服务端返回了 code != 0，或响应无法解析。 */
class ApiException(message: String) : Exception(message)

/**
 * 桌面端只读 HTTP 服务的客户端。
 * 统一走 {code,msg,data} 信封（code == 0 才算成功），见 main/server/routes.js。
 */
class ApiClient(private val prefs: Prefs) {

    companion object {
        /** /quotes 的 codes 上限，超过要切片（服务端 maxBatchCodes = 50） */
        private const val BATCH = 50

        /**
         * runCatching 的取消安全版：CancellationException 必须继续向上抛，
         * 否则协程的取消会被吞成 Result.failure，结构化并发就断了。
         */
        private inline fun <T> catching(block: () -> T): Result<T> = try {
            Result.success(block())
        } catch (e: CancellationException) {
            throw e
        } catch (e: Throwable) {
            Result.failure(e)
        }
    }

    /** 最近一次响应里 X-Mooquant-Source 的值（qmt / mock），供状态条展示。 */
    @Volatile
    var lastSource: String = ""
        private set

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .build()

    /** 批量行情；codes 超过 50 个自动切片并发。 */
    suspend fun quotes(codes: List<String>): Result<List<Quote>> = withContext(Dispatchers.IO) {
        catching {
            if (codes.isEmpty()) return@catching emptyList()
            coroutineScope {
                codes.chunked(BATCH)
                    .map { chunk -> async { fetchQuotes(chunk) } }
                    .awaitAll()
                    .flatten()
            }
        }
    }

    /** 搜索代码 / 名称 / 拼音首字母。服务端对这个接口返回裸数组，不是信封里的对象。 */
    suspend fun search(q: String, limit: Int = 20): Result<List<SearchItem>> = withContext(Dispatchers.IO) {
        catching {
            if (q.isBlank()) return@catching emptyList()
            val (_, json) = call("/search", mapOf("q" to q, "limit" to limit.toString()))
            val arr = json.optJSONArray("data") ?: JSONArray()
            (0 until arr.length()).mapNotNull { i ->
                val o = arr.optJSONObject(i) ?: return@mapNotNull null
                SearchItem(
                    code = o.optString("code"),
                    name = o.optString("name"),
                    industry = o.optString("industry"),
                )
            }
        }
    }

    /** 连通性测试。成功返回数据源模式（qmt / mock）。 */
    suspend fun testConnection(): Result<String> = withContext(Dispatchers.IO) {
        catching { call("/status", emptyMap()).first }
    }

    // ---- 内部 ----

    private fun fetchQuotes(codes: List<String>): List<Quote> {
        val (_, json) = call("/quotes", mapOf("codes" to codes.joinToString(","), "skipError" to "1"))
        val data = json.optJSONObject("data") ?: return emptyList()
        val arr = data.optJSONArray("quotes") ?: return emptyList()
        return (0 until arr.length()).mapNotNull { i ->
            val o = arr.optJSONObject(i) ?: return@mapNotNull null
            Quote(
                code = o.optString("code"),
                name = o.optString("name"),
                price = o.optDouble("price", 0.0),
                change = o.optDouble("change", 0.0),
                changePercent = o.optDouble("changePercent", 0.0),
                prevClose = o.optDouble("prevClose", 0.0),
            )
        }
    }

    /**
     * 发一个 GET，返回 (数据源模式, 信封根对象)。
     * 无论 HTTP 状态码如何都读响应体 —— 服务端出错时返回的仍是完整信封，错误信息在 msg 里。
     */
    private fun call(path: String, params: Map<String, String>): Pair<String, JSONObject> {
        val base = prefs.apiBase
        if (base.isEmpty()) throw ApiException("尚未配置服务器地址")

        val url = (base + path).toHttpUrlOrNull()
            ?: throw ApiException("服务器地址不合法：$base")

        val builder: HttpUrl.Builder = url.newBuilder()
        params.forEach { (k, v) -> builder.addQueryParameter(k, v) }
        prefs.token.takeIf { it.isNotEmpty() }?.let { builder.addQueryParameter("token", it) }

        val request = Request.Builder().url(builder.build()).get().build()

        client.newCall(request).execute().use { resp ->
            val source = resp.header("X-Mooquant-Source").orEmpty()
            lastSource = source
            val text = resp.body?.string().orEmpty()
            if (text.isBlank()) {
                throw ApiException("服务器无响应内容（HTTP ${resp.code}）")
            }
            val json = try {
                JSONObject(text)
            } catch (e: Exception) {
                throw ApiException("服务器返回了非 JSON 内容（HTTP ${resp.code}）")
            }
            val code = json.optInt("code", -1)
            if (code != 0) {
                throw ApiException(json.optString("msg").ifBlank { "请求失败（code=$code）" })
            }
            return source to json
        }
    }
}
