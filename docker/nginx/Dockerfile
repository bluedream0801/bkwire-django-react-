FROM node:16.13-alpine as build-stage

# Build Vars
ARG BUILD

# Auth0 Vars
ARG AUTH0_DOMAIN
ARG AUTH0_CLIENT_ID
ENV NX_APP_AUTH0_DOMAIN=$AUTH0_DOMAIN
ENV NX_APP_AUTH0_CLIENT_ID=$AUTH0_CLIENT_ID

ARG CLIENT_SECRET
ENV NX_APP_CLIENT_SECRET=$CLIENT_SECRET

ARG AUTH0_AUDIENCE
ARG AUTH0_SCOPE
ENV NX_APP_AUTH0_AUDIENCE=$AUTH0_AUDIENCE
ENV NX_APP_AUTH0_SCOPE=$AUTH0_SCOPE

# AWS Vars
ARG AWS_ACCESS_KEY_ID
ARG AWS_SECRET_ACCESS_KEY
ENV AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID
ENV AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY

# API Vars
ARG API_URL
ENV NX_API_URL=$API_URL

# Stripe Vars
ARG STRIPE_API_KEY
ARG STRIPE_TABLE_ID
ARG STRIPE_TABLE_KEY
ARG STRIPE_WEBHOOK_SECRET
ARG STRIPE_RETURN_URL

ENV NX_APP_STRIPE_API_KEY=$STRIPE_API_KEY
ENV NX_APP_STRIPE_TABLE_ID=$STRIPE_TABLE_ID
ENV NX_APP_STRIPE_TABLE_KEY=$STRIPE_TABLE_KEY
ENV NX_APP_STRIPE_WEBHOOK_SECRET=$STRIPE_WEBHOOK_SECRET
ENV NX_APP_STRIPE_RETURN_URL=$STRIPE_RETURN_URL


WORKDIR /app

COPY ./frontend/package.json /app/
COPY ./frontend/package-lock.json /app/
#RUN npm ci -S
RUN npm install


COPY ./frontend /app
COPY ./docker/nginx/conf.d/index.html.${BUILD} /app/apps/bkwire/src/index.html
RUN npm run build -S


FROM nginx:alpine AS frontend

ARG BUILD

COPY --from=build-stage /app/dist/apps/bkwire /var/www/html
COPY ./docker/nginx/conf.d/${BUILD}.conf /etc/nginx/conf.d/default.conf
COPY ./docker/nginx/nginx.conf /etc/nginx/nginx.conf
