"""Build the signed Android companion with a JDK and Android SDK build tools."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("jdk", "tools", "android-jar", "keystore", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    scanner = source / "lib/zxing-core-3.5.3.jar"
    binary = args.jdk.resolve() / "bin"
    tools = args.tools.resolve()
    platform = args.android_jar.resolve()
    output = args.output.resolve()
    keystore = args.keystore.resolve()
    if not os.environ.get("AGENTSBOT_SIGN_PASSWORD"):
        parser.error("Set AGENTSBOT_SIGN_PASSWORD to the signing key password")
    if not keystore.exists():
        subprocess.run([binary / "keytool", "-genkeypair", "-alias", "agentsbot",
                        "-keyalg", "RSA", "-keysize", "3072", "-validity", "10000",
                        "-dname", "CN=Agents Bot", "-keystore", keystore,
                        "-storepass:env", "AGENTSBOT_SIGN_PASSWORD",
                        "-keypass:env", "AGENTSBOT_SIGN_PASSWORD"], check=True)
    with TemporaryDirectory() as staging:
        work = Path(staging)
        def run(*command):
            subprocess.run(command, cwd=work, check=True)
        (work / "classes").mkdir()
        run(tools / "aapt2", "compile", "--dir", source / "res", "-o", "resources.zip")
        run(tools / "aapt2", "link", "-I", platform, "--manifest",
            source / "AndroidManifest.xml", "-o", "base.apk", "resources.zip")
        run(binary / "javac", "--release", "8", "-encoding", "UTF-8",
            "-classpath", os.pathsep.join(map(str, [platform, scanner])), "-d", "classes",
            source / "MonitorActivity.java", source / "ScanActivity.java")
        run(binary / "jar", "cf", "classes.jar", "-C", "classes", ".")
        run(binary / "java", "-cp", tools / "lib/d8.jar", "com.android.tools.r8.D8",
            "--min-api", "26", "--lib", platform, "--output", ".", "classes.jar", scanner)
        with ZipFile(work / "base.apk", "a") as archive:
            archive.write(work / "classes.dex", "classes.dex")
        run(tools / "zipalign", "-f", "4", "base.apk", "aligned.apk")
        signer = [binary / "java", "-jar", tools / "lib/apksigner.jar"]
        run(*signer, "sign", "--ks", keystore, "--ks-key-alias", "agentsbot",
            "--ks-pass", "env:AGENTSBOT_SIGN_PASSWORD", "--out", "signed.apk", "aligned.apk")
        run(*signer, "verify", "--verbose", "signed.apk")
        shutil.copyfile(work / "signed.apk", output)


if __name__ == "__main__":
    main()
