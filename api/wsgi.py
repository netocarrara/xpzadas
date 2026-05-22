"""WSGI entry point for Vercel deployment"""
from index import app

if __name__ == "__main__":
    app.run()
