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

