You can start testing Siproxylin on Windows, **it is in "win" branch**.

What you need to do is:

1. install Python 3.11.9 (that's latest 3.11 available for windows) - https://www.python.org/downloads/release/python-3119/
2. install Git https://git-scm.com/download/win
3. make sure they're both in the path
4. in Windows search type bash - this will give you Git terminal, run it
5. Run commands:
    - cd desktop
    - git clone https://github.com/confund0/siproxylin.git
    - cd siproxylin
    - git checkout win
7. from the same CLI (git-bash) do 
    - pip install slixmpp==1.8.5
    - pip install -r requirements.txt
8. You can try running python main.py


● Windows Build Requirements for drunk_call_service

  1. Visual Studio 2019 or 2022 (Community Edition is fine)

  - Install "Desktop development with C++" workload
  - Make sure C++17 support is included

  2. CMake (3.15 or newer)

  - Download: https://cmake.org/download/
  - Add to PATH during installation

  3. vcpkg (Microsoft's C++ package manager - easiest way)

  git clone https://github.com/Microsoft/vcpkg.git
  cd vcpkg
  bootstrap-vcpkg.bat
  vcpkg integrate install

  4. Install C++ dependencies via vcpkg:

  vcpkg install grpc:x64-windows
  vcpkg install protobuf:x64-windows
  vcpkg install spdlog:x64-windows
  vcpkg install glib:x64-windows

  5. GStreamer for Windows

  - Download: https://gstreamer.freedesktop.org/download/
  - Install both:
    - Runtime installer (gstreamer-1.0-msvc-x86_64-*.msi)
    - Development installer (gstreamer-1.0-devel-msvc-x86_64-*.msi)
  - Choose Complete installation
  - Add to PATH: C:\gstreamer\1.0\msvc_x86_64\bin
  - Set env vars:
  GSTREAMER_1_0_ROOT_MSVC_X86_64=C:\gstreamer\1.0\msvc_x86_64

Should fix PATH: 

export PATH="/c/Program Files/gstreamer/1.0/msvc_x86_64/bin:$HOME/Desktop/vcpkg/installed/x64-windows/bin:$PATH"

