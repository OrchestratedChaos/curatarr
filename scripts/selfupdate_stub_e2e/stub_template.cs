using System;
using System.Net;
using System.Text;
using System.Threading;

class Program {
    static void Main(string[] args) {
        string mode = "__MODE__";
        string version = "__VERSION__";

        if (mode == "crash") {
            Environment.Exit(1);
            return;
        }
        if (mode == "hang") {
            Thread.Sleep(Timeout.Infinite);
            return;
        }

        string portStr = Environment.GetEnvironmentVariable("CURATARR_UI_PORT") ?? "8787";
        int port = int.Parse(portStr);

        var listener = new HttpListener();
        listener.Prefixes.Add("http://127.0.0.1:" + port + "/");
        listener.Start();

        while (true) {
            HttpListenerContext ctx;
            try {
                ctx = listener.GetContext();
            } catch (Exception) {
                break;
            }
            var req = ctx.Request;
            var resp = ctx.Response;
            string body;
            if (req.Url.AbsolutePath == "/healthz") {
                body = "{\"version\": \"" + version + "\"}";
                resp.ContentType = "application/json";
            } else {
                resp.StatusCode = 404;
                body = "not found";
            }
            byte[] buf = Encoding.UTF8.GetBytes(body);
            resp.ContentLength64 = buf.Length;
            resp.OutputStream.Write(buf, 0, buf.Length);
            resp.OutputStream.Close();
        }
    }
}
