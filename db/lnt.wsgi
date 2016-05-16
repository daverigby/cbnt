#!/Users/matt/lnt/venv/bin/python
# -*- Python -*-

import lnt.server.ui.app

application = lnt.server.ui.app.App.create_standalone(
  '/Users/matt/lnt/db/lnt.cfg')

if __name__ == "__main__":
    import werkzeug
    werkzeug.run_simple('Matts-MacBook-Pro.local', 8000, application)
