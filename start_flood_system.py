"""Run the flood assessment app from any working directory."""
import sys
from pathlib import Path
import fastapi

sys.path.insert(0, str(Path(__file__).resolve().parent / 'flood_risk_system'))

if __name__ == '__main__':
    from app.main import main
    main()
