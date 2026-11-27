# fnOS离线构建

构建环境需要Python 3.11以上、根目录requirements.lock及fnpack；目标运行时仍是fnOS的python312。使用根锁建立独立构建环境后运行：

```sh
python packaging/fnos/AngeMedia/build.py --python /path/to/build-venv/bin/python --output-dir /path/to/artifacts
```

下载器分别按x86_64/aarch64、CPython3.12解析同一锁，接受glibc2.28及更早兼容标签，并只下载二进制轮子。正式清单为wheelhouse.SHA256SUMS；集合、哈希、目标标签、名称/版本、Requires-Python及包含extra/环境标记的传递依赖闭包均须通过，才会进入套件。外部wheelhouse可通过`--wheelhouse /path/to/wheels`提供，同样执行全部校验；不会自动改写清单。

依赖更新时先在新的独立目录实际下载两种架构的全部轮子，复核官方来源、元数据与闭包，再更新哈希清单并构建/解包验收。不能只追加某几个ARM扩展名，也不能由文件数量推断架构完整。清单和根锁必须随同一批更新。

新包上线前仍需实际目标安装、启动、升级及回退验证；ARM64轮子存在、标签和闭包通过均不等于ARM64运行通过。构建命令只产出本地FPK，不安装或发布套件。
