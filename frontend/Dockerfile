FROM node:22-alpine
WORKDIR /app
# Lockfile is required: `npm install` without one resolves fresh versions at build time,
# so two builds of the same commit could ship different dependency trees.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
CMD ["npm","start"]
