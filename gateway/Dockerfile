FROM nginx:alpine

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY install/ /usr/share/nginx/html/install/
COPY static/ /usr/share/nginx/html/static/

# No environment variable substitution needed — config is static
ENV NGINX_ENVSUBST_FILTER=NONE_NEEDED
