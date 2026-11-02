import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'backend')))

from backend.app import app

if __name__ == '__main__':
    app.run(debug=True)
