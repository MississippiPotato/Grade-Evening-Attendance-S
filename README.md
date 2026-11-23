# 青岛软件学院、计算机科学与技术学院晚点名报备

面向中国石油大学（华东）青岛软件学院、计算机科学与技术学院的晚点名工具。网页托管在 GitHub Pages，浏览器使用 IndexedDB 保存任务和回执。独立服务提供账号登录和特殊情况邮箱。

| 入口 | 主要功能 |
| --- | --- |
| 学生端 | 查看任务、定位打卡、离校报备、下载个人回执 |
| 班级负责人端 | 查看本班报备分布、批量导入回执、生成提醒、导出统计、报送特殊情况 |
| 管理端 | 发布、延期和撤销任务，查看班级完成率，导出年级数据，接收特殊情况 |

## 本地预览和部署

在仓库根目录运行下列命令，然后访问 `http://localhost:8765/`。

```powershell
python -m http.server 8765 --directory dist
```

网站首页直接进入登录页。「查看演示」位于登录页底部。学生端提供「模拟校内」和「模拟校外」，演示记录与正式记录分别保存。真实账户登录、任务发布与同步、在线报送需要运行下面的服务。

正式部署时，把 `dist/` 作为 HTTPS 站点的发布目录。不同域名、协议、端口和浏览器使用各自的本机数据。启用正式定位前，需在实际使用的手机浏览器内测试许可、精度和围栏边界。

## 登录

输入用户名和密码后，系统会按账号角色进入对应工作台。退出登录位于各端顶部，退出后仍保留此浏览器里的本机工作记录。登录会话最长 8 小时。

| 角色 | 用户名 | 密码 | 首次登录结果 |
| --- | --- | --- | --- |
| 学生 | 学籍姓名 | 学号 | 进入学生端，打卡及报备回执自动填入姓名、班级和学号 |
| 班级负责人 | 大写 `A` + 学籍姓名 | 学号 | 进入本人所属班级，可导入并查看该班名单和回执 |
| 管理员 | 部署时设定的用户名 | 部署时设定的密码 | 进入管理端，初始化全部学籍和负责人名单 |

### 账号与报送服务

本地开发可通过服务本身提供网页。生产环境把服务绑定到回环地址，用已有的 HTTPS 反向代理发布 `/attendance-api/`，并在启动参数中通过 `--origin` 指定 GitHub Pages 的站点来源。生产接口地址位于 `dist/assets/auth.mjs`。

首次部署时，在本机从两份工作簿创建私有数据库。旧账号签发密钥用于生成相同的账号标识，以延续浏览器上的历史记录。通过 `--admin-username` 指定管理员用户名。使用 `--generate-admin-password` 可在私有输出目录生成管理员密码文件。

```powershell
python -m pip install openpyxl cryptography
python tools/seed_server.py --students "../private/students.xlsx" --leaders "../private/leaders.xlsx" --signing-key "../private/account-signing-key.pem" --output "../private/attendance" --generate-admin-password
python server/service.py --db "../private/attendance/attendance.sqlite3" --pepper-file "../private/attendance/pepper.bin" --site-dir dist
```

工作簿需包含「姓名」「班级」「学号」三列。通过 `http://localhost:8791/` 打开本地联调页面。部署时只将 `attendance.sqlite3` 和 `pepper.bin` 通过私有渠道复制到服务的数据目录，并限制文件权限。此初始化工具面向全新数据库。

## 正式办理

1. **管理端准备。** 使用管理员账户登录，核对自动加载的名单。管理端可以用 UTF-8 CSV 更新本机任务名单。服务端登录账号由后台名册管理。新建任务默认勾选唐岛湾校区，围栏允许边界外延 20 米，定位误差上限为 70 米。其他校区使用 WGS 84 的 GeoJSON Polygon 或 MultiPolygon，允许以 Feature 包装。
2. **发布。** 新建任务，设置北京时间、适用班级和允许校区，确认应报人数后发布。任务按班级自动同步，负责人的工作台随任务加载本班应报名单。延长截止时间和撤销也会自动同步。
3. **填报。** 学生登录后打开本班任务。在校路线使用手机定位，离校路线填写原因。个人回执下载后私发本班负责人。
4. **汇总。** 负责人导入学生回执，核对班级报备分布，导出班级统计给管理员。需要管理员单独处理的事项使用「报送特殊情况」。
5. **收齐。** 管理端导入班级统计，比较已汇总班级的完成率。特殊情况单独在系统内的「特殊情况邮箱」查阅。

各端底部的“本机数据”提供本地备份下载、最近导入摘要以及已截止或撤销任务的数据清理。备份是供留档的 JSON 快照。跨设备日常交接使用各端的任务包、回执和班级报送包。公开部署只需上传 `dist/`，真实学籍、负责人名单、回执和正式报送数据应通过本机和约定的私聊渠道处理。

登录、线上任务和特殊情况报送由服务端处理。旧版任务按其已有本机数据处理。回执、提醒和日常统计延续原有文件与链接流程。

## 目录与检查

| 路径 | 用途 |
| --- | --- |
| `dist/` | 静态页面、样式、应用模块和校园素材 |
| `server/service.py` | 账号认证和特殊情况邮箱 API |
| `tools/seed_server.py` | 从两份工作簿初始化服务端账号 |
| `tests/` | 业务规则和三端浏览器测试 |

用 `node --test tests/model.test.mjs` 检查统计、定位和文件导入规则。安装 Playwright 和 Chromium 后运行 `node tests/browser.mjs`，测试会启动一个使用临时账号的本地服务。可通过 `ATTENDANCE_PLAYWRIGHT` 指定模块位置，通过 `ATTENDANCE_CHROMIUM` 指定 Chromium 可执行文件。

## 校园素材

- 徽标来自学院官网页头。 [青岛软件学院、计算机科学与技术学院](https://computer.upc.edu.cn/)
- 校园夜景为图说石大中的《夜间石大》，摄影肖鸿伟。 [靓丽风景](https://photo.upc.edu.cn/jlfj.htm)
- 唐岛湾校区定位边界取自 OpenStreetMap way 222840628（v22），由 © OpenStreetMap contributors 提供，数据按 [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) 分发。 [边界文件](dist/assets/tangdaowan-boundary.geojson) · [学校平面图](https://upce.upc.edu.cn/2018/0404/c8300a143081/pagem.htm)
- 校区名称参照学校公开资料。 [学校简介](https://www.upc.edu.cn/info/1012/1002.htm)
