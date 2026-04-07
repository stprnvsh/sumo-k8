FROM node:20-slim

WORKDIR /app

COPY package.json .
RUN npm install --no-audit --no-fund

COPY pages ./pages
COPY next.config.js .

RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]

