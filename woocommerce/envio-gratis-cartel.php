<?php
/**
 * Cartel "ENVÍO GRATIS" para Tienda de Drones (WooCommerce).
 *
 * Muestra un cartel sobre la imagen (en la lista de productos y en la ficha) y un
 * texto debajo del precio en los productos que cuestan $400.000 o más.
 * El cartel sigue al precio: si el producto baja de $400.000, desaparece solo.
 *
 * Instalación: plugin WPCode → Code Snippets → Add Snippet → "Add Your Custom Code"
 * → tipo "PHP Snippet" → pegar este código (sin la primera línea "<?php") →
 * Insert Method "Auto Insert", Location "Run Everywhere" → Activar → Guardar.
 */

// Monto desde el que el envío es gratis (igual que en WooCommerce → Envío).
if ( ! defined( 'DT_ENVIO_GRATIS_DESDE' ) ) {
	define( 'DT_ENVIO_GRATIS_DESDE', 400000 );
}

function dt_tiene_envio_gratis( $product ) {
	if ( ! $product instanceof WC_Product ) {
		return false;
	}
	$precio = (float) wc_get_price_to_display( $product );  // en variables, el precio "desde"
	return $precio >= DT_ENVIO_GRATIS_DESDE;
}

// Cartel sobre la imagen en la lista de productos (tienda, categorías, búsqueda).
add_action( 'woocommerce_before_shop_loop_item_title', function () {
	global $product;
	if ( dt_tiene_envio_gratis( $product ) ) {
		echo '<span class="dt-envio-gratis">ENVÍO GRATIS</span>';
	}
}, 9 );

// Cartel sobre la imagen principal en la ficha del producto.
add_action( 'woocommerce_before_single_product_summary', function () {
	global $product;
	if ( dt_tiene_envio_gratis( $product ) ) {
		echo '<span class="dt-envio-gratis dt-en-ficha">ENVÍO GRATIS</span>';
	}
}, 19 );

// Texto debajo del precio en la ficha.
add_action( 'woocommerce_single_product_summary', function () {
	global $product;
	if ( dt_tiene_envio_gratis( $product ) ) {
		echo '<p class="dt-envio-texto">🚚 <strong>Envío gratis</strong> a todo el país</p>';
	}
}, 11 );

// Estilos del cartel (color, tamaño y posición se cambian acá).
add_action( 'wp_head', function () {
	?>
	<style>
		ul.products li.product { position: relative; }
		.single-product div.product { position: relative; }
		.dt-envio-gratis {
			position: absolute; top: 10px; right: 10px; z-index: 9;
			background: #0a7d3b; color: #fff; font-weight: 700; font-size: 12px;
			line-height: 1; padding: 6px 9px; border-radius: 4px; letter-spacing: .5px;
			pointer-events: none;
		}
		.dt-envio-gratis.dt-en-ficha { top: 15px; left: 15px; right: auto; font-size: 14px; }
		.dt-envio-texto { color: #0a7d3b; margin: 6px 0 14px; font-size: 16px; }
	</style>
	<?php
} );
