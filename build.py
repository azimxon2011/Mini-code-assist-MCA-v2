import os
import sys
import subprocess
import shutil

def log(msg):
    print(f"[Build] {msg}")

def run(cmd):
    log(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

def main():
    project_root = os.path.dirname(os.path.abspath(__file__))
    venv_dir = os.path.join(project_root, ".venv-build")
    
    # 1. Create VENV
    if not os.path.exists(venv_dir):
        log("Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
    
    # 2. Identify Binaries
    if sys.platform == "win32":
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
        pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe")
        pyinstaller_exe = os.path.join(venv_dir, "Scripts", "pyinstaller.exe")
        target_script = "omni_assistant.py"
    else:
        python_exe = os.path.join(venv_dir, "bin", "python")
        pip_exe = os.path.join(venv_dir, "bin", "pip")
        pyinstaller_exe = os.path.join(venv_dir, "bin", "pyinstaller")
        target_script = "omni_assistant.py" # Use cross-platform version on Linux
    
    # 3. Install Dependencies
    log("Installing dependencies...")
    run([pip_exe, "install", "--upgrade", "pip", "setuptools", "wheel"])
    
    req_file = os.path.join(project_root, "requirements.txt")
    run([pip_exe, "install", "-r", req_file])
    
    if sys.platform == "win32":
        run([pip_exe, "install", "pywin32"])
    
    # 4. Run PyInstaller
    log(f"Building {target_script}...")
    
    dist_path = os.path.join(project_root, "dist")
    build_path = os.path.join(project_root, "build_temp")
    
    # Clean old builds
    for d in [dist_path, build_path]:
        if os.path.exists(d):
            shutil.rmtree(d)

    cmd = [
        pyinstaller_exe,
        "--onefile",
        "--noconsole",
        "--name", "omni_assistant",
        "--clean",
        "--distpath", dist_path,
        "--workpath", build_path,
        os.path.join(project_root, target_script)
    ]
    
    # Add icon if exists
    icon_path = os.path.join(project_root, "python_icon.ico")
    if os.path.exists(icon_path):
        cmd.extend(["--icon", icon_path])
        
    run(cmd)
    
    log("Build complete! Check the 'dist' folder.")

if __name__ == "__main__":
    main()
