FROM node:22-alpine
WORKDIR /app
# Lockfile is required: `npm install` without one resolves fresh versions at build time,
# so two builds of the same commit could ship different dependency trees.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Bounded heap so the build fails loudly rather than being OOM-killed by the container.
# Raise together with Docker's memory limit if the build outgrows it.
ENV NODE_OPTIONS=--max-old-space-size=1024
RUN npm run build
CMD ["npm","start"]
