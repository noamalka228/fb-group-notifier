FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# Install dependencies first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium

# Copy project files
COPY . .

# Expose API port
EXPOSE 8000

# Run the FastAPI server
CMD ["python", "monitor.py"]
