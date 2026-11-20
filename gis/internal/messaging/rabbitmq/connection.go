// Package rabbitmq provides the RabbitMQ connection, a publisher, and consumers
// used by the worker. A single durable topic exchange is declared on connect;
// each consumer declares and binds its own queue.
package rabbitmq

import (
	"fmt"

	"gis/internal/config"

	amqp "github.com/rabbitmq/amqp091-go"
)

// Connection wraps an AMQP connection and a dedicated publishing channel, and
// owns the topic exchange.
type Connection struct {
	conn     *amqp.Connection
	pubCh    *amqp.Channel
	exchange string
}

// Connect dials RabbitMQ, opens a publishing channel, and declares the exchange.
func Connect(cfg config.RabbitMQConfig) (*Connection, error) {
	conn, err := amqp.Dial(cfg.URL)
	if err != nil {
		return nil, fmt.Errorf("dial rabbitmq: %w", err)
	}

	ch, err := conn.Channel()
	if err != nil {
		conn.Close()
		return nil, fmt.Errorf("open channel: %w", err)
	}

	if err := ch.ExchangeDeclare(
		cfg.Exchange, amqp.ExchangeTopic,
		true,  // durable
		false, // auto-deleted
		false, // internal
		false, // no-wait
		nil,
	); err != nil {
		ch.Close()
		conn.Close()
		return nil, fmt.Errorf("declare exchange: %w", err)
	}

	return &Connection{conn: conn, pubCh: ch, exchange: cfg.Exchange}, nil
}

// Exchange returns the topic exchange name.
func (c *Connection) Exchange() string { return c.exchange }

// publishChannel returns the shared publishing channel.
func (c *Connection) publishChannel() *amqp.Channel { return c.pubCh }

// openChannel opens a fresh channel (each consumer uses its own).
func (c *Connection) openChannel() (*amqp.Channel, error) { return c.conn.Channel() }

// Ping reports whether the connection is still open (used by readiness checks).
func (c *Connection) Ping() error {
	if c.conn.IsClosed() {
		return fmt.Errorf("rabbitmq connection closed")
	}
	return nil
}

// Close tears down the publishing channel and the connection.
func (c *Connection) Close() error {
	var chErr error
	if c.pubCh != nil {
		chErr = c.pubCh.Close()
	}
	if c.conn != nil {
		if err := c.conn.Close(); err != nil {
			return err
		}
	}
	return chErr
}
