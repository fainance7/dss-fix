package rabbitmq

import (
	"context"
	"fmt"

	amqp "github.com/rabbitmq/amqp091-go"
)

// Publisher publishes messages to the connection's exchange.
type Publisher struct {
	conn *Connection
}

// NewPublisher returns a Publisher bound to the given connection.
func NewPublisher(conn *Connection) *Publisher {
	return &Publisher{conn: conn}
}

// Publish sends a JSON-encoded body to the exchange using the given routing key.
func (p *Publisher) Publish(ctx context.Context, routingKey string, body []byte) error {
	err := p.conn.publishChannel().PublishWithContext(ctx,
		p.conn.Exchange(),
		routingKey,
		false, // mandatory
		false, // immediate
		amqp.Publishing{
			ContentType:  "application/json",
			DeliveryMode: amqp.Persistent,
			Body:         body,
		},
	)
	if err != nil {
		return fmt.Errorf("publish to %q: %w", routingKey, err)
	}
	return nil
}
