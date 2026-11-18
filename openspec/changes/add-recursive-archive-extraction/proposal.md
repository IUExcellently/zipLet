# 递归解压归档

## Why

多层归档可能使用不同密码或伪装后缀，手动处理难以追踪每层结果。全量解压产生的中间文件也容易耗尽磁盘。

## What Changes

- 新增本地 CLI，从环境变量获取路径和并发配置，递归扫描输入目录与解包结果。
- 按实际格式识别 7z、ZIP、RAR、WIM，支持伪装后缀。
- 从本地候选文件试验密码，按归档和层级记录可复用的确认结果。
- 以外层归档为单位控制并发，校验并发布媒体后清理本任务生成的中间归档。

## Capabilities

### New Capabilities

- `recursive-archive-extraction` 支持归档发现、逐层解包、密码记录、媒体发布及清理。

### Modified Capabilities

## Impact

涉及 Python CLI 入口、本地状态文件和输出目录。计划使用官方 7-Zip 命令行，并验证所用版本对目标样本的支持。
