using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Launcher
{
    [STAThread]
    private static void Main()
    {
        string home = AppDomain.CurrentDomain.BaseDirectory;
        string bundled = Path.Combine(home, "runtime", "pythonw.exe");
        var start = new ProcessStartInfo();
        start.FileName = File.Exists(bundled) ? bundled : "pythonw.exe";
        start.WorkingDirectory = home;
        start.Arguments = "-E -s \"" + Path.Combine(home, "app.py") + "\"";
        start.UseShellExecute = false;
        try
        {
            Process.Start(start);
        }
        catch (Exception error)
        {
            MessageBox.Show("请解压完整的 Windows 便携包后再启动。\n" + error.Message,
                "Agents Bot", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
