# Dashboard 乱码与其他组件字号变化（2026-11-19）

现场首页在 DOCTYPE 前输出了 163 字节二进制，包含 `Mac OS X`、`ATTR`、`com.apple.provenance`。来源为 `/usr/local/www/widgets/include/._traccar.inc`，文件魔数 `0005160700020000`，属于 macOS AppleDouble 元数据，不是 PHP 代码。

pfSense 首页自动 include 该目录的 `.inc` 文件；这个隐藏文件同样被加载，二进制内容输出在 HTML 声明之前。它与实际小组件 CSS 无关，却导致浏览器进入 quirks 模式，改变其他表格组件的默认字号。

使用相同原生首页 HTML、同一套实际 CSS 和同一个 Chrome 视口比较：

| 项目 | 清理前 | 清理后 |
| --- | --- | --- |
| DOCTYPE 前额外输出 | 163 字节 | 无，DOCTYPE 位于第 0 字节 |
| `document.compatMode` | `BackCompat` | `CSS1Compat` |
| body 字号 | 14px | 14px |
| 系统信息表格字号 | 16px | 14px |
| 网关表格字号 | 16px | 14px |
| 组件标题字号 | 16px | 16px |

首页其他组件字体没有被强行重设；移除文档模式干扰后自动恢复。

## 现场处理

识别 widget 目录内 8 个魔数匹配的 AppleDouble 普通文件，核实不属于当前 pkg 清单后，保留目录结构移至 `/conf/traccar/quarantine/appledouble-20261119-144246/`。未删除数据，未修改 pfSense 核心文件、其他组件样式或布局。

现场安装版本已为 0.3.6，与本工作区版本不同，因此没有重新安装旧版本，也未覆盖现有采集或解算程序。GPS/RTK 服务没有重启。

这些文件不属于已安装包，可能是在目录复制或归档解包时附带进入；仅凭现场不能确认具体传输操作。隔离现有残留和预防未来打包污染分别处理。

## 防止复发

- 构建复制 package 文件和 Python 源码时，递归过滤 `._*`、`.DS_Store`、`__MACOSX` 与 Python 缓存。
- 生成包文件清单前再次检查暂存目录；存在元数据就拒绝生成包。
- Git 忽略同类 macOS 文件。
- 原生 Dashboard 渲染检查新增断言：DOCTYPE 前不得有非空白输出，避免仅验证小组件自身而漏掉全页模式变化。
- 增加递归复制过滤及生成清单前防护测试。目录手工复制或归档解包同样应排除这些元数据，避免绕过包构建流程。
