# Use the official Python 3.12.3 image as a base
FROM python:3.12.3-slim

# Set the working directory in the container
WORKDIR /app

# Copy and install requirements.txt, copied first for faster building
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code
COPY celeritas/ ./celeritas/
COPY main.py .

# Create a directory for persistent cache data
RUN mkdir -p /app/data

# Run the application
CMD ["python", "main.py"]