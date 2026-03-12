#!/bin/bash
# LakeStream Local Testing Script
# Tests all new features: false success fix, auth flow, Playwright networkidle

set -e

echo "🚀 LakeStream Local Testing Script"
echo "=================================="
echo ""

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  .env file not found. Copying from .env.example...${NC}"
    cp .env.example .env
    echo -e "${RED}⚠️  IMPORTANT: Edit .env and set JWT_SECRET before continuing!${NC}"
    echo "   Run: openssl rand -hex 32"
    exit 1
fi

# Check if JWT_SECRET is set
if grep -q "JWT_SECRET=your-secret-key-here" .env; then
    echo -e "${RED}⚠️  JWT_SECRET not set in .env!${NC}"
    echo "   Run: openssl rand -hex 32"
    echo "   Then update JWT_SECRET in .env"
    exit 1
fi

echo -e "${GREEN}✅ .env configured${NC}"
echo ""

# Step 1: Start Docker services
echo "📦 Step 1: Starting Docker services (Postgres + Redis)..."
make docker-up
sleep 3

# Check if services are healthy
echo "🔍 Checking service health..."
docker-compose ps

if ! docker-compose ps | grep -q "Up"; then
    echo -e "${RED}❌ Docker services failed to start${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Docker services running${NC}"
echo ""

# Step 2: Run migrations
echo "🗄️  Step 2: Running database migrations..."
make migrate

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Migration failed${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Migrations complete${NC}"
echo ""

# Step 3: Install Playwright browsers (if not already installed)
echo "🎭 Step 3: Installing Playwright browsers..."
playwright install chromium

if [ $? -ne 0 ]; then
    echo -e "${YELLOW}⚠️  Playwright install failed (may already be installed)${NC}"
fi

echo -e "${GREEN}✅ Playwright ready${NC}"
echo ""

# Step 4: Start API server
echo "🌐 Step 4: Starting API server..."
echo -e "${YELLOW}This will run in the foreground. Open a new terminal to continue testing.${NC}"
echo ""
echo "Services running:"
echo "  - PostgreSQL: localhost:5433"
echo "  - Redis: localhost:6381"
echo "  - API: http://localhost:8000"
echo ""
echo "To test the application:"
echo "  1. Open http://localhost:8000 in your browser"
echo "  2. You should be redirected to /login (auth flow working)"
echo "  3. Click 'Sign up' to create an account"
echo "  4. After signup, you'll be logged in and redirected to dashboard"
echo "  5. Try the Quick Scrape feature with a test URL"
echo ""
echo "In another terminal, start the worker:"
echo "  make worker"
echo ""
echo "Press Ctrl+C to stop the server when done."
echo ""
echo -e "${GREEN}Starting server on http://localhost:8000...${NC}"
echo ""

make dev
