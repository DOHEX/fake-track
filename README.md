# fake-track

校园跑接口调试工具。按小程序接口顺序执行登录、建单、检查、提交和轨迹上传，支持路网生成轨迹、WGS84/GCJ02 坐标转换，以及 Web 控制台。

> [!IMPORTANT]
> 本项目仅用于接口调试、协议分析和研究学习。请遵守学校与平台规则。

## 快速开始

```powershell
copy .env.example .env
uv sync
uv run fake-track run
```

`.env` 至少填写三项：

```env
FAKE_TRACK_KEY=      # AES 密钥，16/24/32 字节
FAKE_TRACK_PHONE=    # 手机号
FAKE_TRACK_PASSWORD= # 密码
```

多账号配置参见 `fake-track.example.toml` 的 `[[accounts]]` 段落。

## CLI 命令

| 命令 | 用途 |
|------|------|
| `run` | 执行一次完整跑步测试 |
| `counts` | 查看晨跑、普通跑、有效次数 |
| `recordlist` | 查看跑步记录列表，支持筛选 |
| `encrypt TEXT` | 使用小程序同格式输出密文 |
| `serve` | 启动 Web 控制台 |

### 常用参数

| 参数 | 命令 | 说明 |
|------|------|------|
| `--account NAME_OR_INDEX` | `run`, `counts`, `recordlist` | 选择账号，可重复传入 |
| `--json-output` | `run`, `counts`, `recordlist` | 输出 JSON |
| `--skip-wait` | `run` | 跳过模拟跑步等待 |
| `--force-submit` | `run` | checkRecord 不通过仍继续提交 |
| `--ignore-target-met` | `run` | 已达到目标次数仍继续跑 |
| `--run-type morning\|normal` | `recordlist` | 按晨跑/普跑筛选 |
| `--status valid\|invalid` | `recordlist` | 按有效/无效筛选 |
| `--semester TEXT` | `recordlist` | 按学期筛选，如 `"2025-2026 第二学期"` |
| `--track-image` | `run` | 输出轨迹叠加图 |
| `--report-path PATH` | `run` | 额外写 JSON 报告到文件 |

`encrypt` 只需要 `FAKE_TRACK_KEY`，不要求手机号和密码。

## Web 控制台

```powershell
uv run fake-track serve
```

仪表板展示每个账号的跑步进度、今日完成状态，支持一键运行和查看历史记录（可筛选）。

## 配置

### 环境变量

| 变量 | 说明 |
|------|------|
| `FAKE_TRACK_KEY` | AES 密钥（兼容旧名 `FAKE_TRACK_SECRET`） |
| `FAKE_TRACK_PHONE` | 手机号（多账号时可省略） |
| `FAKE_TRACK_PASSWORD` | 密码（多账号时可省略） |
| `FAKE_TRACK_IGNORE_TARGET_MET` | 等价 `--ignore-target-met`，接受 `1/true/yes/on` |

### TOML 配置

`fake-track.toml`（可选，无此文件时使用内置默认值）：

```powershell
copy fake-track.example.toml fake-track.toml
```

主要配置项：

| Section | 字段 | 说明 |
|---------|------|------|
| `[run]` | `start_lat`, `start_lng` | 起点坐标 |
| `[run]` | `target_distance_km`, `target_pace_min_per_km` | 目标距离和配速 |
| `[route]` | `road_routing_enabled`, `road_map_path` | 路网开关和 OSM 路径 |
| `[output]` | `report_path` | JSON 报告输出路径 |
| `[[accounts]]` | `name`, `phone`, `password` | 多账号配置 |

详见 [fake-track.example.toml](fake-track.example.toml)。

## GitHub Actions

[Daily Campus Run](.github/workflows/daily-run.yml) 每天 06:05（晨跑）和 14:00（普跑）自动运行，也支持手动触发。

需配置 Secrets：`FAKE_TRACK_KEY`、`FAKE_TRACK_PHONE`、`FAKE_TRACK_PASSWORD`。

可选 Variables：`FAKE_TRACK_IGNORE_TARGET_MET`。
