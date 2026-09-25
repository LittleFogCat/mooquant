package tech.xiaoniu.mooquant.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketException
import java.net.SocketTimeoutException
import kotlin.coroutines.coroutineContext

/**
 * 局域网服务发现：找桌面端的 UDP 广播信标（main/server/discovery-beacon.js）。
 *
 * 两阶段：
 *  1. 主动 —— 向受限广播 + 各网段定向广播地址发探包，信标单播应答（快，通常一次往返）
 *  2. 被动 —— 阶段一没结果时绑 12679 等一条周期广播（每 3s 一条，兜底路径）
 *
 * 报文里没有 token 明文，只有 tokenRequired 布尔。
 */
class Discovery(context: Context) {

    companion object {
        private const val MAGIC = "MOOQUANT-DISCOVERY"
        private const val PORT = 12679
        private const val PROBE = """{"magic":"$MAGIC","v":1,"probe":true}"""

        /** 单个 receive 的阻塞上限，用于让取消检查和超时判断有机会跑 */
        private const val SLICE_MS = 250

        private const val MAX_PACKET = 1500
    }

    private val appContext = context.applicationContext

    /**
     * 找不到时返回 null（含超时、无网络、服务端未开信标）。不抛异常。
     */
    suspend fun discover(timeoutMs: Long = 1500, passiveMs: Long = 4000): ServerTarget? =
        withContext(Dispatchers.IO) {
            probe(timeoutMs) ?: listen(passiveMs)
        }

    // ---- 阶段一：主动探测 ----

    private suspend fun probe(timeoutMs: Long): ServerTarget? {
        val targets = broadcastTargets()
        if (targets.isEmpty()) return null

        val probe = PROBE.toByteArray(Charsets.UTF_8)
        val deadline = System.currentTimeMillis() + timeoutMs

        try {
            DatagramSocket().use { sock ->
                sock.broadcast = true
                sock.soTimeout = SLICE_MS

                for (addr in targets) {
                    // 网卡不支持广播 / 临时路由问题：换下一个目标，不整体失败
                    try {
                        sock.send(DatagramPacket(probe, probe.size, addr, PORT))
                    } catch (e: Exception) {
                        // 忽略
                    }
                }

                val buf = ByteArray(MAX_PACKET)
                while (System.currentTimeMillis() < deadline) {
                    coroutineContext.ensureActive()
                    val pkt = DatagramPacket(buf, buf.size)
                    try {
                        sock.receive(pkt)
                    } catch (e: SocketTimeoutException) {
                        continue
                    } catch (e: Exception) {
                        return null
                    }
                    parse(String(pkt.data, 0, pkt.length, Charsets.UTF_8))?.let { return it }
                }
            }
        } catch (e: SocketException) {
            // 无法建 socket（少见）：交给被动路径
            return null
        }
        return null
    }

    /** 受限广播 + 每个网段的定向广播地址（多网卡机器上只有受限广播时另一个网段收不到）。 */
    private fun broadcastTargets(): List<InetAddress> {
        val out = LinkedHashSet<InetAddress>()

        try {
            out.add(InetAddress.getByName("255.255.255.255"))
        } catch (e: Exception) {
            // 忽略
        }

        val cm = appContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        val linkProps = try {
            cm?.activeNetwork?.let { cm.getLinkProperties(it) }
        } catch (e: Exception) {
            null
        }

        linkProps?.linkAddresses?.forEach { la ->
            val addr = la.address
            if (addr is Inet4Address && !addr.isLoopbackAddress && !addr.isLinkLocalAddress) {
                val prefix = la.prefixLength
                if (prefix in 1..31) {
                    directedBroadcast(addr.address, prefix)?.let { out.add(it) }
                }
            }
        }

        return out.toList()
    }

    private fun directedBroadcast(ip: ByteArray, prefix: Int): InetAddress? {
        if (ip.size != 4) return null
        val ipInt = ((ip[0].toInt() and 0xFF) shl 24) or
            ((ip[1].toInt() and 0xFF) shl 16) or
            ((ip[2].toInt() and 0xFF) shl 8) or
            (ip[3].toInt() and 0xFF)
        val mask = -1 shl (32 - prefix)
        val bcast = ipInt or mask.inv()
        return try {
            InetAddress.getByAddress(
                byteArrayOf(
                    ((bcast ushr 24) and 0xFF).toByte(),
                    ((bcast ushr 16) and 0xFF).toByte(),
                    ((bcast ushr 8) and 0xFF).toByte(),
                    (bcast and 0xFF).toByte(),
                )
            )
        } catch (e: Exception) {
            null
        }
    }

    // ---- 阶段二：被动监听周期广播 ----

    private suspend fun listen(passiveMs: Long): ServerTarget? {
        val wifi = appContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        // Android 不持 MulticastLock 收不到广播/组播包
        val lock = try {
            wifi?.createMulticastLock("mooquant")?.apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (e: Exception) {
            null
        }

        try {
            DatagramSocket(null).use { sock ->
                sock.reuseAddress = true
                sock.soTimeout = SLICE_MS
                try {
                    sock.bind(InetSocketAddress(PORT))
                } catch (e: Exception) {
                    return null
                }

                val deadline = System.currentTimeMillis() + passiveMs
                val buf = ByteArray(MAX_PACKET)
                while (System.currentTimeMillis() < deadline) {
                    coroutineContext.ensureActive()
                    val pkt = DatagramPacket(buf, buf.size)
                    try {
                        sock.receive(pkt)
                    } catch (e: SocketTimeoutException) {
                        continue
                    } catch (e: Exception) {
                        return null
                    }
                    parse(String(pkt.data, 0, pkt.length, Charsets.UTF_8))?.let { return it }
                }
            }
        } catch (e: SocketException) {
            return null
        } finally {
            try {
                lock?.release()
            } catch (e: Exception) {
                // 忽略
            }
        }
        return null
    }

    // ---- 报文解析 ----

    /** 探包应答与周期广播同构，都接受；urls[0] 已经是 API 基地址。 */
    private fun parse(text: String): ServerTarget? {
        val o = try {
            JSONObject(text)
        } catch (e: Exception) {
            return null
        }
        if (o.optString("magic") != MAGIC) return null
        val urls = o.optJSONArray("urls") ?: return null
        if (urls.length() == 0) return null
        val base = urls.optString(0).trim().trimEnd('/')
        if (base.isEmpty()) return null
        return ServerTarget(
            apiBase = base,
            source = o.optString("source").ifBlank { "unknown" },
            tokenRequired = o.optBoolean("tokenRequired", false),
        )
    }
}
