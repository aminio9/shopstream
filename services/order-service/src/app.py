"""
ShopStream Order Service
========================
Handles order creation, processing, and status management.
Integrates with RabbitMQ for async processing.
"""

import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from functools import wraps

import mysql.connector
import pika
from flask import Flask, jsonify, request
from flask_cors import CORS
from mysql.connector import pooling
from decimal import Decimal, InvalidOperation

app = Flask(__name__)
CORS(app)



def as_decimal(x, field):
    try:
        return Decimal(str(x))
    except (InvalidOperation, TypeError):
        raise ValueError(f"Invalid {field}: {x}")

def as_int(x, field):
    try:
        return int(x)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid {field}: {x}")

# ============================================
# Configuration
# ============================================


class Config:
    DB_CONFIG = {
        "host": os.environ.get("DB_HOST", "mariadb"),
        "port": int(os.environ.get("DB_PORT", 3306)),
        "user": os.environ.get("DB_USER", "appuser"),
        "password": os.environ.get("DB_PASSWORD", "apppassword"),
        "database": os.environ.get("DB_NAME", "shopstream"),
    }

    RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "rabbitmq")
    RABBITMQ_PORT = int(os.environ.get("RABBITMQ_PORT", 5672))
    RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "guest")
    RABBITMQ_PASS = os.environ.get("RABBITMQ_PASSWORD", "guest")

    PRODUCT_SERVICE_URL = os.environ.get(
        "PRODUCT_SERVICE_URL", "http://product-service:3000"
    )


# ============================================
# Database Connection
# ============================================

db_pool = None


def init_db_pool():
    global db_pool
    try:
        db_pool = pooling.MySQLConnectionPool(
            pool_name="order_pool",
            pool_size=5,
            pool_reset_session=True,
            **Config.DB_CONFIG,
        )
        print("✓ Database pool initialized")
        return True
    except Exception as e:
        print(f"✗ Database pool error: {e}")
        return False


def get_db():
    if db_pool is None:
        init_db_pool()
    return db_pool.get_connection()


def init_tables():
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                status ENUM('pending', 'processing', 'shipped', 'delivered', 'cancelled') DEFAULT 'pending',
                subtotal DECIMAL(10,2) NOT NULL,
                shipping DECIMAL(10,2) DEFAULT 0,
                total DECIMAL(10,2) NOT NULL,
                shipping_address TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_user (user_id),
                INDEX idx_status (status),
                INDEX idx_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id INT AUTO_INCREMENT PRIMARY KEY,
                order_id INT NOT NULL,
                product_id INT NOT NULL,
                product_name VARCHAR(255) NOT NULL,
                price DECIMAL(10,2) NOT NULL,
                quantity INT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
                INDEX idx_order (order_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                order_id INT NOT NULL,
                status VARCHAR(50) NOT NULL,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
                INDEX idx_order (order_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        conn.commit()
        cursor.close()
        conn.close()
        print("✓ Database tables initialized")
        return True
    except Exception as e:
        print(f"✗ Table initialization error: {e}")
        return False


# ============================================
# RabbitMQ Connection
# ============================================

rabbitmq_connection = None
rabbitmq_channel = None


def init_rabbitmq():
    global rabbitmq_connection, rabbitmq_channel

    try:
        credentials = pika.PlainCredentials(Config.RABBITMQ_USER, Config.RABBITMQ_PASS)
        parameters = pika.ConnectionParameters(
            host=Config.RABBITMQ_HOST,
            port=Config.RABBITMQ_PORT,
            credentials=credentials,
            heartbeat=600,
            blocked_connection_timeout=300,
        )

        rabbitmq_connection = pika.BlockingConnection(parameters)
        rabbitmq_channel = rabbitmq_connection.channel()

        # Declare queues
        rabbitmq_channel.queue_declare(queue="order_processing", durable=True)
        rabbitmq_channel.queue_declare(queue="order_notifications", durable=True)
        rabbitmq_channel.queue_declare(queue="inventory_updates", durable=True)

        print("✓ RabbitMQ connected")
        return True
    except Exception as e:
        print(f"⚠ RabbitMQ not available: {e}")
        return False


def publish_message(queue: str, message: dict):
    """Publish a message to RabbitMQ queue."""
    global rabbitmq_channel

    if rabbitmq_channel is None:
        init_rabbitmq()

    if rabbitmq_channel:
        try:
            rabbitmq_channel.basic_publish(
                exchange="",
                routing_key=queue,
                body=json.dumps(message, default=str),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Persistent
                    content_type="application/json",
                ),
            )
            return True
        except Exception as e:
            print(f"Failed to publish message: {e}")
            # Try to reconnect
            init_rabbitmq()

    return False


# ============================================
# Helper Functions
# ============================================


def decimal_default(obj):
    """JSON encoder for Decimal types."""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def get_user_id():
    """Get user ID from request headers (set by API Gateway)."""
    return request.headers.get("X-User-Id")


def add_order_history(order_id: int, status: str, message: str = None):
    """Add entry to order history."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO order_history (order_id, status, message) VALUES (%s, %s, %s)",
            (order_id, status, message),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Failed to add order history: {e}")


# ============================================
# Health Check
# ============================================


@app.route("/health", methods=["GET"])
def health_check():
    health = {
        "status": "healthy",
        "service": "order-service",
        "timestamp": datetime.utcnow().isoformat(),
    }

    # Check database
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
        conn.close()
        health["database"] = "connected"
    except Exception as e:
        health["database"] = "disconnected"
        health["status"] = "degraded"

    # Check RabbitMQ
    if rabbitmq_connection and rabbitmq_connection.is_open:
        health["rabbitmq"] = "connected"
    else:
        health["rabbitmq"] = "disconnected"
        health["status"] = "degraded"

    status_code = 200 if health["status"] == "healthy" else 503
    return jsonify(health), status_code


# ============================================
# Order Routes
# ============================================


@app.route("/orders", methods=["GET"])
def get_orders():
    """Get orders for the current user."""
    user_id = get_user_id()

    if not user_id:
        return jsonify({"error": "User ID required"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """SELECT id, user_id, status, subtotal, shipping, total, 
                      shipping_address, created_at, updated_at
               FROM orders 
               WHERE user_id = %s 
               ORDER BY created_at DESC""",
            (user_id,),
        )
        orders = cursor.fetchall()

        # Get items for each order
        for order in orders:
            cursor.execute(
                """SELECT product_id, product_name as name, price, quantity
                   FROM order_items WHERE order_id = %s""",
                (order["id"],),
            )
            order["items"] = cursor.fetchall()

            # Convert datetime objects
            if order["created_at"]:
                order["createdAt"] = order["created_at"].isoformat()
                del order["created_at"]
            if order["updated_at"]:
                order["updatedAt"] = order["updated_at"].isoformat()
                del order["updated_at"]

        cursor.close()
        conn.close()

        return app.response_class(
            response=json.dumps(orders, default=decimal_default),
            status=200,
            mimetype="application/json",
        )

    except Exception as e:
        print(f"Get orders error: {e}")
        return jsonify({"error": "Failed to fetch orders"}), 500


@app.route("/orders/<int:order_id>", methods=["GET"])
def get_order(order_id):
    """Get a specific order."""
    user_id = get_user_id()

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """SELECT * FROM orders WHERE id = %s AND user_id = %s""",
            (order_id, user_id),
        )
        order = cursor.fetchone()

        if not order:
            cursor.close()
            conn.close()
            return jsonify({"error": "Order not found"}), 404

        # Get items
        cursor.execute(
            """SELECT product_id, product_name as name, price, quantity
               FROM order_items WHERE order_id = %s""",
            (order_id,),
        )
        order["items"] = cursor.fetchall()

        # Get history
        cursor.execute(
            """SELECT status, message, created_at
               FROM order_history WHERE order_id = %s ORDER BY created_at""",
            (order_id,),
        )
        order["history"] = cursor.fetchall()

        cursor.close()
        conn.close()

        return app.response_class(
            response=json.dumps(order, default=str),
            status=200,
            mimetype="application/json",
        )

    except Exception as e:
        print(f"Get order error: {e}")
        return jsonify({"error": "Failed to fetch order"}), 500



@app.route("/orders", methods=["POST"])
def create_order():
    user_id = get_user_id()
    if not user_id:
        return jsonify({"error": "User ID required"}), 400

    # Header user id -> int (DB column is INT)
    try:
        user_id_int = as_int(user_id, "X-User-Id")
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400

    data = request.get_json(silent=True) or {}
    items = data.get("items")

    if not isinstance(items, list) or not items:
        return jsonify({"error": "Order items are required"}), 400

    try:
        # Normalize + validate items so we never do int + str math
        norm_items = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"items[{i}] must be an object")

            # validate required fields
            if item.get("productId") is None:
                raise ValueError(f"Missing items[{i}].productId")
            if not item.get("name"):
                raise ValueError(f"Missing items[{i}].name")

            price = as_decimal(item.get("price"), f"items[{i}].price")   # Decimal
            qty = as_int(item.get("quantity"), f"items[{i}].quantity")   # int

            if qty <= 0:
                raise ValueError(f"items[{i}].quantity must be > 0")
            if price < 0:
                raise ValueError(f"items[{i}].price must be >= 0")

            norm_items.append({
                "productId": as_int(item.get("productId"), f"items[{i}].productId"),
                "name": str(item.get("name")),
                "price": price,
                "quantity": qty,
            })

        # Totals (force Decimal start to avoid sum() starting at int 0)
        subtotal = sum((it["price"] * it["quantity"] for it in norm_items), start=Decimal("0"))
        shipping = Decimal("0") if subtotal > Decimal("50") else Decimal("9.99")
        total = subtotal + shipping

        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO orders (user_id, subtotal, shipping, total, shipping_address, notes)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    user_id_int,
                    subtotal,
                    shipping,
                    total,
                    data.get("shippingAddress"),
                    data.get("notes"),
                ),
            )
            order_id = cursor.lastrowid

            for it in norm_items:
                cursor.execute(
                    """INSERT INTO order_items (order_id, product_id, product_name, price, quantity)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (order_id, it["productId"], it["name"], it["price"], it["quantity"]),
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

        add_order_history(order_id, "pending", "Order created")

        # Publish normalized items (no string prices sneaking through)
        publish_message(
            "order_processing",
            {
                "orderId": order_id,
                "userId": user_id_int,
                "items": [
                    {
                        "productId": it["productId"],
                        "name": it["name"],
                        "price": str(it["price"]),   # serialize Decimal safely
                        "quantity": it["quantity"],
                    }
                    for it in norm_items
                ],
                "subtotal": str(subtotal),
                "shipping": str(shipping),
                "total": str(total),
            },
        )

        return jsonify(
            {
                "id": order_id,
                "orderId": order_id,
                "status": "pending",
                "subtotal": float(subtotal),
                "shipping": float(shipping),
                "total": float(total),
                "message": "Order created successfully",
            }
        ), 201

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        print(f"Create order error: {e}")
        return jsonify({"error": "Failed to create order"}), 500


@app.route("/orders/<int:order_id>/cancel", methods=["POST"])
def cancel_order(order_id):
    """Cancel an order."""
    user_id = get_user_id()

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # Check order exists and belongs to user
        cursor.execute(
            "SELECT id, status FROM orders WHERE id = %s AND user_id = %s",
            (order_id, user_id),
        )
        order = cursor.fetchone()

        if not order:
            cursor.close()
            conn.close()
            return jsonify({"error": "Order not found"}), 404

        if order["status"] not in ("pending", "processing"):
            cursor.close()
            conn.close()
            return jsonify(
                {"error": f"Cannot cancel order with status: {order['status']}"}
            ), 400

        # Get order items for inventory restore
        cursor.execute(
            "SELECT product_id, quantity FROM order_items WHERE order_id = %s",
            (order_id,),
        )
        items = cursor.fetchall()

        # Update order status
        cursor.execute(
            "UPDATE orders SET status = 'cancelled' WHERE id = %s", (order_id,)
        )

        conn.commit()
        cursor.close()
        conn.close()

        # Add to history
        add_order_history(order_id, "cancelled", "Order cancelled by user")

        # Restore inventory
        for item in items:
            publish_message(
                "inventory_updates",
                {
                    "productId": item["product_id"],
                    "quantity": item["quantity"],  # Positive to restore
                    "orderId": order_id,
                    "reason": "order_cancelled",
                },
            )

        # Notify user
        publish_message(
            "order_notifications",
            {"type": "order_cancelled", "userId": user_id, "orderId": order_id},
        )

        return jsonify({"message": "Order cancelled successfully"})

    except Exception as e:
        print(f"Cancel order error: {e}")
        return jsonify({"error": "Failed to cancel order"}), 500


@app.route("/orders/<int:order_id>/status", methods=["PUT"])
def update_order_status(order_id):
    """Update order status (internal use)."""
    data = request.get_json()

    if not data or "status" not in data:
        return jsonify({"error": "Status is required"}), 400

    new_status = data["status"]
    valid_statuses = ["pending", "processing", "shipped", "delivered", "cancelled"]

    if new_status not in valid_statuses:
        return jsonify(
            {"error": f"Invalid status. Must be one of: {valid_statuses}"}
        ), 400

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT user_id, status FROM orders WHERE id = %s", (order_id,))
        order = cursor.fetchone()

        if not order:
            cursor.close()
            conn.close()
            return jsonify({"error": "Order not found"}), 404

        cursor.execute(
            "UPDATE orders SET status = %s WHERE id = %s", (new_status, order_id)
        )

        conn.commit()
        cursor.close()
        conn.close()

        # Add to history
        add_order_history(
            order_id, new_status, data.get("message", f"Status changed to {new_status}")
        )

        # Notify user
        publish_message(
            "order_notifications",
            {
                "type": "order_update",
                "userId": order["user_id"],
                "orderId": order_id,
                "status": new_status,
            },
        )

        return jsonify({"message": "Order status updated", "status": new_status})

    except Exception as e:
        print(f"Update status error: {e}")
        return jsonify({"error": "Failed to update order status"}), 500


# ============================================
# Admin/Stats Routes
# ============================================


@app.route("/orders/stats", methods=["GET"])
def get_order_stats():
    """Get order statistics."""
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        # Total orders by status
        cursor.execute("""
            SELECT status, COUNT(*) as count, SUM(total) as revenue
            FROM orders
            GROUP BY status
        """)
        by_status = cursor.fetchall()

        # Orders today
        cursor.execute("""
            SELECT COUNT(*) as count, COALESCE(SUM(total), 0) as revenue
            FROM orders
            WHERE DATE(created_at) = CURDATE()
        """)
        today = cursor.fetchone()

        # Orders this week
        cursor.execute("""
            SELECT COUNT(*) as count, COALESCE(SUM(total), 0) as revenue
            FROM orders
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        """)
        week = cursor.fetchone()

        cursor.close()
        conn.close()

        return app.response_class(
            response=json.dumps(
                {"byStatus": by_status, "today": today, "thisWeek": week},
                default=decimal_default,
            ),
            status=200,
            mimetype="application/json",
        )

    except Exception as e:
        print(f"Get stats error: {e}")
        return jsonify({"error": "Failed to fetch statistics"}), 500


# ============================================
# Error Handlers
# ============================================


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500


# ============================================
# Application Startup
# ============================================


def wait_for_db(max_retries=30, retry_interval=2):
    import time

    for i in range(max_retries):
        try:
            if init_db_pool():
                init_tables()
                return True
        except Exception as e:
            print(f"Waiting for database... ({i + 1}/{max_retries})")
            time.sleep(retry_interval)

    return False


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════╗
║         ShopStream Order Service              ║
╠═══════════════════════════════════════════════╣
║  Port: 5000                                   ║
║  Health: /health                              ║
╚═══════════════════════════════════════════════╝
    """)

    if not wait_for_db():
        print("Failed to connect to database")
        sys.exit(1)

    # Initialize RabbitMQ (non-blocking)
    init_rabbitmq()

    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("DEBUG", False))
