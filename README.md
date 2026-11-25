# minecraft-teacker · Minecraft 版本追踪站

定期监测 Minecraft 的版本更新（**正式版 / 快照 / 愚人节快照**），自动下载并对版本 jar 进行"拆包"分析，把全部版本元数据与文件清单展示在 GitHub Pages 上，支持**多选版本同时查看与对比**。

## 在线访问

- 站点地址：`https://xiaowangdeqilin.github.io/minecraft-teacker/`（Actions 首次运行后自动开通）
- 数据更新：GitHub Actions **每 6 小时**自动运行一次

## 功能

| 功能 | 说明 |
| --- | --- |
| 定期监测 | 每 6 小时拉取 Mojang 官方公开版本清单（`version_manifest_v2`） |
| 全版本列表 | 页面列出清单中**全部**版本（正式版 / 快照 / 愚人节 / 旧版），支持类型筛选与搜索 |
| 自动拆包 | 发现新版本后自动下载 client jar，记录内部**文件路径、大小、CRC32** 清单 |
| 多选查看 | 页面上可勾选任意多个版本同时查看详情与文件清单 |
| 版本对比 | 选中恰好 2 个版本可查看**新增 / 移除 / 变更**的文件差异 |
| 手动补拆 | 在 Actions 页手动触发工作流，填入版本 ID 可补拆任意历史版本 |

## 合理使用原则（重要）

本项目**不存储、不转发、不分发任何 Minecraft 游戏文件**：

1. 版本元数据来自 Mojang 官方公开 API；
2. "拆包"仅指在内存中读取 jar 的文件目录结构（路径 / 大小 / CRC32），产物只是几 KB 的清单 JSON；
3. 下载的 jar 用完即弃，不会进入仓库或 Pages。

Minecraft 版权归 Mojang Studios / Microsoft 所有。本项目仅供学习研究。

## 目录结构

```
├── index.html                     # GitHub Pages 前端（纯原生 JS，无构建步骤）
├── scripts/
│   └── fetch_versions.py          # 监测 + 拆包脚本
├── data/
│   ├── index.json                 # 全部版本元数据（页面数据源）
│   ├── versions/<id>.json.gz      # 已拆包版本的文件清单（gzip）
│   └── state.json                 # 状态：已知版本 / 已拆包记录
└── .github/workflows/monitor.yml  # 定时监测 + 部署 Pages
```

## 环境变量（脚本可调）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TRACK_RELEASES` | 30 | 自动追踪最近 N 个正式版 |
| `TRACK_SNAPSHOTS` | 30 | 自动追踪最近 N 个快照 |
| `BACKFILL_VERSION` | 空 | 手动补拆的版本 ID（逗号分隔） |

愚人节快照（`20w14∞`、`24w14potato`、`25w14craftmine` 等）始终全量追踪。

## 许可证

[MIT](./LICENSE)。Minecraft 相关内容版权归 Mojang Studios / Microsoft 所有，本项目与其无隶属关系。
