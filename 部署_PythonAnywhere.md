# 小白 · PythonAnywhere 部署说明

PythonAnywhere 免费版不需要绑卡，给一个固定域名：`https://你的用户名.pythonanywhere.com`。
缺点是后台是英文，免费账号每 3 个月要在后台点一次续期。

## 一、注册

1. 打开 https://www.pythonanywhere.com/registration/register/beginner/
2. 填用户名、邮箱、密码，选 **Beginner**（免费）；
3. 收邮件点验证链接。

用户名会直接变成域名，建议用拼音，比如 `xiaobai-health` 或你的名字缩写。

## 二、把代码拉下来

登录后点顶部 **Consoles** → **Bash**，在黑色终端里输入：

```bash
git clone https://github.com/xiaoman-000/xiaobai-health.git
```

看到 `Resolving deltas: 100%` 就成功了。可以用 `ls xiaobai-health` 确认里面有 `app.py`、`wsgi.py`、`web`、`知识库`。

## 三、创建 Web 应用

1. 点顶部 **Web** → **Add a new web app**；
2. 域名默认就是 `你的用户名.pythonanywhere.com`，直接 Next；
3. 选 **Manual configuration**（不要选 Flask/Django 模板）；
4. Python 版本选 **3.10** 或更高，Next。

## 四、填三个地方

在 Web 页面往下滚，找到这几项：

| 项目 | 填什么 |
| --- | --- |
| Source code | `/home/你的用户名/xiaobai-health` |
| Working directory | `/home/你的用户名/xiaobai-health` |

然后点 **WSGI configuration file** 那一行的链接（形如
`/var/www/你的用户名_pythonanywhere_com_wsgi.py`），把编辑器里的内容**全部删掉**，换成：

```python
import sys

path = '/home/你的用户名/xiaobai-health'
if path not in sys.path:
    sys.path.insert(0, path)

from wsgi import application
```

把 `你的用户名` 换成你自己的，保存。

## 五、上线

1. 回到 Web 页面，点绿色 **Reload 你的用户名.pythonanywhere.com**；
2. 打开 `https://你的用户名.pythonanywhere.com/api/health`；
3. 看到 `{"ok": true, "docs": 20}` 就成了。

顺便在 Web 页面把 **Force HTTPS** 打开，避免浏览器提示不安全。

## 六、以后改了代码怎么更新

在 Bash 控制台：

```bash
cd ~/xiaobai-health
git pull
```

然后回 Web 页面点一次 **Reload**。

## 七、常见问题

**打开是 500 错误**
Web 页面有 **Error log** 链接，点开看最后几行。最常见的是路径里的用户名写错了，或者 `wsgi.py` 没拉到。

**ModuleNotFoundError: No module named 'wsgi'**
Source code / Working directory / WSGI 文件里的 path 三处路径不一致，统一改成 `/home/你的用户名/xiaobai-health`。

**页面能开但发消息没反应**
Web 页面的 Error log 里会有 traceback，把最后 20 行发我。

**提示 CPU 配额用完**
免费账号每天有 CPU 秒数限制，正常对话消耗很小；如果被刷爆，等第二天重置，或在后台看 Usage。

**3 个月后打不开**
免费应用的续期机制，登录后台点一下 **Run until 3 months from today** 即可。

## 八、和 Render 的区别

| | PythonAnywhere 免费 | Render 免费 |
| --- | --- | --- |
| 绑卡 | 不需要 | 需要验证卡 |
| 域名 | 用户名.pythonanywhere.com | 服务名.onrender.com |
| 休眠 | 不休眠，但 CPU 有配额 | 15 分钟无访问休眠，唤醒 30~60 秒 |
| 国内访问 | 一般可直接打开 | 偏慢 |
| 续期 | 每 3 个月点一次 | 无需 |
