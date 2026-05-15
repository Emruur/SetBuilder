import sys
import os
import runpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

if __name__ == '__main__':
    runpy.run_path(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src', 'main.py'),
        run_name='__main__',
    )
